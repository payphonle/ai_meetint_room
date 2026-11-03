"""本地 SQLite 存储层：全量保存群聊/私聊消息，查看历史零网络请求。

- message_targets：群聊消息实际投递给了哪些助手（取消/重发/助手筛选视图的基础）
- quote_of / quote_content：引用转发关系（显示与重发时还原）
"""
import aiosqlite
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  conv_type TEXT NOT NULL,             -- group | private
  platform TEXT,                       -- 助手名（私聊对象 / 群聊回复者）
  role TEXT NOT NULL,                  -- user | assistant
  sender TEXT NOT NULL,                -- 'me' 或 助手名
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'done', -- queued|typing|streaming|done|failed|cancelled
  created_at TEXT NOT NULL DEFAULT (datetime('now','localtime')),
  quote_of INTEGER                     -- 引用的消息 id（可空）
);
CREATE INDEX IF NOT EXISTS idx_messages_conv ON messages(conv_type, platform, id);
CREATE TABLE IF NOT EXISTS message_targets(
  msg_id INTEGER NOT NULL,
  platform TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_targets ON message_targets(platform, msg_id);
"""

# SELECT 列与字典映射（含 LEFT JOIN 出的引用字段）
COLS = ["id", "conv_type", "platform", "role", "sender", "content", "status",
        "created_at", "quote_of", "quote_sender", "quote_content"]

_SELECT = ("SELECT m.id, m.conv_type, m.platform, m.role, m.sender, m.content, "
           "m.status, m.created_at, m.quote_of, q.sender AS quote_sender, "
           "q.content AS quote_content "
           "FROM messages m LEFT JOIN messages q ON q.id = m.quote_of ")


class Storage:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._db: aiosqlite.Connection | None = None

    async def init(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        await self._db.executescript(SCHEMA)
        # 旧库迁移：messages 可能没有 quote_of 列
        try:
            await self._db.execute("ALTER TABLE messages ADD COLUMN quote_of INTEGER")
            await self._db.commit()
        except Exception:
            pass  # 列已存在

    async def close(self):
        if self._db:
            await self._db.close()

    def _to_dict(self, row) -> dict:
        return dict(zip(COLS, row))

    async def add_message(self, conv_type: str, platform, role: str, sender: str,
                          content: str, status: str = "done",
                          quote_of: int | None = None) -> int:
        cur = await self._db.execute(
            "INSERT INTO messages(conv_type, platform, role, sender, content, status, quote_of) "
            "VALUES(?,?,?,?,?,?,?)",
            (conv_type, platform, role, sender, content, status, quote_of))
        await self._db.commit()
        return cur.lastrowid

    async def update_message(self, msg_id: int, content: str | None = None,
                             status: str | None = None):
        sets, args = [], []
        if content is not None:
            sets.append("content=?")
            args.append(content)
        if status is not None:
            sets.append("status=?")
            args.append(status)
        if not sets:
            return
        args.append(msg_id)
        await self._db.execute(
            f"UPDATE messages SET {', '.join(sets)} WHERE id=?", args)
        await self._db.commit()

    async def get_message(self, msg_id: int) -> dict | None:
        cur = await self._db.execute(_SELECT + "WHERE m.id=?", (msg_id,))
        row = await cur.fetchone()
        return self._to_dict(row) if row else None

    async def list_messages(self, conv_type: str, platform: str | None = None) -> list[dict]:
        if conv_type == "group":
            sql = _SELECT + "WHERE m.conv_type='group' ORDER BY m.id"
            args: tuple = ()
        else:
            sql = (_SELECT + "WHERE m.conv_type='private' AND m.platform=? ORDER BY m.id")
            args = (platform,)
        cur = await self._db.execute(sql, args)
        rows = await cur.fetchall()
        return [self._to_dict(r) for r in rows]

    async def list_assistant_thread(self, platform: str) -> list[dict]:
        """助手筛选视图：群聊里该助手收到的问题（投递目标含它）+ 它的所有回复。"""
        sql = (_SELECT + "WHERE m.conv_type='group' AND "
               "(m.sender=? OR m.id IN (SELECT msg_id FROM message_targets WHERE platform=?)) "
               "ORDER BY m.id")
        cur = await self._db.execute(sql, (platform, platform))
        rows = await cur.fetchall()
        return [self._to_dict(r) for r in rows]

    async def set_targets(self, msg_id: int, platforms: list[str]):
        for p in platforms:
            await self._db.execute(
                "INSERT INTO message_targets(msg_id, platform) VALUES(?,?)", (msg_id, p))
        await self._db.commit()

    async def get_targets(self, msg_id: int) -> list[str]:
        cur = await self._db.execute(
            "SELECT platform FROM message_targets WHERE msg_id=? ORDER BY rowid", (msg_id,))
        return [r[0] for r in await cur.fetchall()]
