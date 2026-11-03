"""AI 群聊（方案A）本地服务：FastAPI + WebSocket + 静态页面。

仅绑定 127.0.0.1，不对外网暴露。
"""
import asyncio
import logging
import sys
import urllib.parse
from contextlib import asynccontextmanager
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import yaml
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .storage import Storage
from .router import parse_mentions, strip_mentions
from .scheduler import Scheduler
from .browser import BrowserManager
from .adapters.echo import EchoAdapter
from .adapters.deepseek import DeepSeekAdapter
from .adapters.chatglm import ChatGLMAdapter
from .adapters.qianwen import QianwenAdapter
from .adapters.doubao import DoubaoAdapter
from .adapters.yuanbao import YuanbaoAdapter
from .adapters import common as adapter_common

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"
with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = yaml.safe_load(f)

# ---- 日志：控制台 + data/logs/app.log（滚动） ----
LOG_DIR = ROOT / "data" / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
_fh = RotatingFileHandler(LOG_DIR / "app.log", maxBytes=2_000_000,
                          backupCount=3, encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
logging.getLogger().addHandler(_fh)
logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger("app")

MEMBERS = [m["name"] for m in CONFIG["members"]]
MEMBER_MAP = {m["name"]: m for m in CONFIG["members"]}
SCH = CONFIG.get("scheduler", {})
ECHO_MODE = SCH.get("echo_mode", True)
REAL_PLATFORMS = set(SCH.get("real_platforms", ["DeepSeek"]))


class ConnectionManager:
    def __init__(self):
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket):
        self.active.discard(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in list(self.active):
            try:
                await ws.send_json(data)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


storage = Storage(str(ROOT / CONFIG.get("db_path", "data/chat.db")))
manager = ConnectionManager()
browser_manager = BrowserManager(str(ROOT / "data/profiles"))


def on_platform_info(platform: str, text: str):
    """适配器向 UI 推送提示（如"请在浏览器中手动登录"）。"""
    asyncio.get_running_loop().create_task(
        manager.broadcast({"type": "platform_info", "platform": platform, "text": text}))


# 各平台节奏参数：真实平台用成员配置，回声平台用 echo 配置；daily_cap 始终取成员配置
echo_cfg = SCH.get("echo", {})
platform_cfg = {}
for m in CONFIG["members"]:
    use_real = (not ECHO_MODE) and m["name"] in REAL_PLATFORMS
    d = m if use_real else echo_cfg
    platform_cfg[m["name"]] = {
        "min_delay": d.get("min_delay", 1.0),
        "max_delay": d.get("max_delay", 3.0),
        "daily_cap": m.get("daily_cap"),
    }

scheduler = Scheduler(storage, manager.broadcast, platform_cfg,
                      quiet_hours=SCH.get("quiet_hours"))

# 各真实平台适配器类（全部 5 平台已接入）
ADAPTER_CLASSES = {
    "DeepSeek": DeepSeekAdapter,
    "智谱清言": ChatGLMAdapter,
    "千问": QianwenAdapter,
    "豆包": DoubaoAdapter,
    "元宝": YuanbaoAdapter,
}

# 适配器注册：真实平台走浏览器适配器，未接入的平台继续回声
if ECHO_MODE:
    _echo = EchoAdapter()
    for _name in MEMBERS:
        scheduler.register_adapter(_name, _echo)
else:
    for _name in MEMBERS:
        if _name in REAL_PLATFORMS and _name in ADAPTER_CLASSES:
            scheduler.register_adapter(_name, ADAPTER_CLASSES[_name](
                browser_manager=browser_manager, on_info=on_platform_info))
        else:
            scheduler.register_adapter(_name, EchoAdapter())


@asynccontextmanager
async def lifespan(_: FastAPI):
    await storage.init()
    scheduler.start()
    yield
    await scheduler.stop()
    await browser_manager.close_all()
    await storage.close()


app = FastAPI(title="AI Group Chat", lifespan=lifespan)


class SendBody(BaseModel):
    text: str
    conv_type: str = "group"          # group | private
    platform: str | None = None       # conv_type=private 时的对象
    quote_msg_id: int | None = None   # 引用的消息 id


class SettingsBody(BaseModel):
    quiet_hours: list[str] | None = None
    members: list[dict] | None = None


async def _compose_deliver(text: str, quote_msg_id: int | None) -> str:
    """投递给助手的正文：剥 @ 标记；有引用时把引用内容（截断 500 字）拼在前面。"""
    deliver = strip_mentions(text, MEMBERS) or text
    if quote_msg_id:
        q = await storage.get_message(quote_msg_id)
        if q and (q.get("content") or "").strip():
            deliver = f"{q['content'][:500]}\n\n{deliver}"
    return deliver


async def _enqueue_user_message(msg_id: int, text: str, quote_msg_id: int | None,
                                conv_type: str, targets: list[str], platform=None):
    """写投递目标（群聊）、入队并广播排队状态。"""
    deliver = await _compose_deliver(text, quote_msg_id)
    if conv_type == "group":
        await storage.set_targets(msg_id, targets)
        for t in targets:
            await scheduler.enqueue(t, {"msg_id": msg_id, "text": deliver,
                                        "conv_type": "group"})
    else:
        await scheduler.enqueue(platform, {"msg_id": msg_id, "text": deliver,
                                           "conv_type": "private"})
    await storage.update_message(msg_id, status="queued")
    await manager.broadcast({"type": "message_update", "id": msg_id, "status": "queued"})
    logger.info("msg %s queued -> %s (conv=%s, quote=%s)",
                msg_id, targets or platform, conv_type, quote_msg_id)


@app.get("/api/state")
async def state():
    return {"members": CONFIG["members"], "echo_mode": ECHO_MODE,
            "real_platforms": sorted(REAL_PLATFORMS)}


@app.get("/api/platforms")
async def platforms():
    return {"platforms": scheduler.snapshot()}


@app.post("/api/platform/{name}/resume")
async def resume_platform(name: str):
    if name not in MEMBERS:
        return JSONResponse({"error": "未知助手"}, status_code=400)
    scheduler.resume(name)
    await manager.broadcast({"type": "platform_state", "platform": name,
                             "paused": False, "reason": ""})
    return {"ok": True}


@app.get("/api/messages")
async def messages(conv: str = "group", platform: str | None = None):
    if conv == "assistant" and platform:
        rows = await storage.list_assistant_thread(platform)
    else:
        rows = await storage.list_messages(conv, platform)
    return {"messages": rows}


@app.post("/api/send")
async def send(body: SendBody):
    text = body.text.strip()
    if not text:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    if body.conv_type == "private":
        if body.platform not in MEMBERS:
            return JSONResponse({"error": "未知助手"}, status_code=400)
        msg_id = await storage.add_message("private", body.platform, "user", "me",
                                           text, quote_of=body.quote_msg_id)
        await manager.broadcast({"type": "new_message",
                                 "message": await storage.get_message(msg_id)})
        await _enqueue_user_message(msg_id, text, body.quote_msg_id,
                                    "private", [body.platform], platform=body.platform)
        return {"ok": True, "targets": [body.platform]}

    targets, mentioned_all = parse_mentions(text, MEMBERS)
    msg_id = await storage.add_message("group", None, "user", "me", text,
                                       quote_of=body.quote_msg_id)
    await manager.broadcast({"type": "new_message",
                             "message": await storage.get_message(msg_id)})
    await _enqueue_user_message(msg_id, text, body.quote_msg_id,
                                "group", targets)
    return {"ok": True, "targets": targets,
            "all": mentioned_all or len(targets) == len(MEMBERS)}


@app.post("/api/cancel/{msg_id}")
async def cancel_msg(msg_id: int):
    msg = await storage.get_message(msg_id)
    if not msg or msg["sender"] != "me":
        return JSONResponse({"error": "消息不存在"}, status_code=404)
    if msg["status"] not in ("queued", "typing"):
        return JSONResponse({"error": "消息已在回复中或已结束，无法取消"}, status_code=409)
    scheduler.cancel(msg_id)
    await storage.update_message(msg_id, status="cancelled")
    await manager.broadcast({"type": "message_update", "id": msg_id,
                             "status": "cancelled"})
    logger.info("msg %s cancelled by user (status was %s)", msg_id, msg["status"])
    return {"ok": True}


@app.post("/api/resend/{msg_id}")
async def resend_msg(msg_id: int):
    msg = await storage.get_message(msg_id)
    if not msg or msg["sender"] != "me":
        return JSONResponse({"error": "消息不存在"}, status_code=404)
    if msg["status"] not in ("failed", "cancelled"):
        return JSONResponse({"error": "仅失败或已取消的消息可重发"}, status_code=409)

    text = msg["content"]
    if msg["conv_type"] == "group":
        targets = await storage.get_targets(msg_id)
        if not targets:
            targets, _ = parse_mentions(text, MEMBERS)
        new_id = await storage.add_message("group", None, "user", "me", text,
                                           status="queued",
                                           quote_of=msg["quote_of"])
        await manager.broadcast({"type": "new_message",
                                 "message": await storage.get_message(new_id)})
        await _enqueue_user_message(new_id, text, msg["quote_of"], "group", targets)
        return {"ok": True, "new_id": new_id, "targets": targets}

    platform = msg["platform"]
    if platform not in MEMBERS:
        return JSONResponse({"error": "未知助手"}, status_code=400)
    new_id = await storage.add_message("private", platform, "user", "me", text,
                                       status="queued", quote_of=msg["quote_of"])
    await manager.broadcast({"type": "new_message",
                             "message": await storage.get_message(new_id)})
    await _enqueue_user_message(new_id, text, msg["quote_of"], "private",
                                [platform], platform=platform)
    return {"ok": True, "new_id": new_id, "targets": [platform]}


@app.get("/api/settings")
async def get_settings():
    return {"quiet_hours": SCH.get("quiet_hours"),
            "members": [{"name": m["name"], "risk": m["risk"],
                         "min_delay": m["min_delay"], "max_delay": m["max_delay"],
                         "daily_cap": m["daily_cap"]} for m in CONFIG["members"]]}


@app.post("/api/settings")
async def save_settings(body: SettingsBody):
    if body.quiet_hours:
        SCH["quiet_hours"] = [str(x) for x in body.quiet_hours][:2]
        scheduler.set_quiet_hours(SCH["quiet_hours"])
    for m in body.members or []:
        cfg = MEMBER_MAP.get(m.get("name"))
        if not cfg:
            continue
        try:
            if m.get("min_delay") is not None:
                cfg["min_delay"] = max(1.0, float(m["min_delay"]))
            if m.get("max_delay") is not None:
                cfg["max_delay"] = max(cfg["min_delay"], float(m["max_delay"]))
            if m.get("daily_cap") is not None:
                cfg["daily_cap"] = max(1, int(m["daily_cap"]))
        except (TypeError, ValueError):
            continue
        pc = scheduler.platform_cfg.get(cfg["name"])
        if pc:
            pc["min_delay"] = cfg["min_delay"]
            pc["max_delay"] = cfg["max_delay"]
            pc["daily_cap"] = cfg["daily_cap"]
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        yaml.safe_dump(CONFIG, f, allow_unicode=True, sort_keys=False)
    logger.info("settings updated via UI")
    return {"ok": True, "note": "间隔/限额/安静时段已即时生效"}


@app.get("/api/export")
async def export_md(conv: str = "group", platform: str | None = None):
    if conv == "assistant" and platform:
        rows = await storage.list_assistant_thread(platform)
        title = f"群聊记录 · {platform} 视角"
        fname = f"群聊记录-{platform}.md"
    elif conv == "private" and platform:
        rows = await storage.list_messages("private", platform)
        title = f"私聊记录 · {platform}"
        fname = f"私聊记录-{platform}.md"
    else:
        rows = await storage.list_messages("group")
        title = "AI 群聊记录"
        fname = "群聊记录.md"

    lines = [f"# {title}", "", f"> 导出时间：{datetime.now():%Y-%m-%d %H:%M}", ""]
    last_date = None
    for m in rows:
        created = m["created_at"] or ""
        date = created[:10]
        if date != last_date:
            lines += [f"## {date}", ""]
            last_date = date
        who = "我" if m["sender"] == "me" else m["sender"]
        lines += [f"**{who}** {created[11:16]}"]
        if m.get("quote_content"):
            preview = m["quote_content"][:80].replace("\n", " ")
            lines += [f"> 引用 {m.get('quote_sender')}：{preview}"]
        lines += ["", m["content"] or "（无内容）", ""]
    return Response("\n".join(lines), media_type="text/markdown; charset=utf-8",
                    headers={"Content-Disposition":
                             f"attachment; filename*=UTF-8''{urllib.parse.quote(fname)}"})


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await manager.connect(ws)
    try:
        while True:
            await ws.receive_text()  # 客户端心跳；服务端事件主动推送
    except WebSocketDisconnect:
        manager.disconnect(ws)


@app.post("/api/debug/open/{platform}")
async def debug_open(platform: str, url: str | None = None):
    """调试：手动打开平台页面（可用 url 指定具体会话），供探针排查用。"""
    cls = ADAPTER_CLASSES.get(platform)
    if not cls:
        return {"error": "未知平台"}
    mod = sys.modules.get(cls.__module__)
    base = getattr(cls, "BASE_URL", None) or getattr(mod, "BASE_URL", None)
    if not base:
        return {"error": "该平台适配器未定义 BASE_URL"}
    try:
        page = await browser_manager.get_page(platform, url or base)
        return {"ok": True, "url": page.url}
    except Exception as e:
        return {"error": str(e)}


@app.get("/api/debug/dom/{platform}")
async def debug_dom(platform: str, probe: str | None = None):
    """诊断接口：输出平台页面的 URL、输入相关元素清单和截图（人工排查用）。

    probe=1 时额外返回各回复块选择器实际抓到的文本（用于修正提取范围）。
    """
    page = browser_manager._pages.get(platform)
    if page is None or page.is_closed():
        return {"error": "浏览器未打开", "tip": "先向该平台发一条消息让浏览器打开"}
    try:
        url = page.url
        elements = await page.evaluate(
            """() => Array.from(document.querySelectorAll(
                   'textarea, div[contenteditable="true"], [data-testid], input[type="text"]'
               )).slice(0, 160).map(el => ({
                   tag: el.tagName.toLowerCase(),
                   id: el.id || null,
                   cls: (el.className && el.className.toString)
                        ? el.className.toString().slice(0, 100) : null,
                   testid: el.getAttribute('data-testid'),
                   placeholder: el.getAttribute('placeholder'),
                   visible: !!(el.offsetWidth || el.offsetHeight
                               || el.getClientRects().length),
                   editable: !!el.isContentEditable,
               }))""")
        shot = ROOT / "data" / "debug" / f"{platform}.png"
        shot.parent.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(shot))
        except Exception as e:
            shot = None
            screenshot_err = str(e)
        out = {"url": url, "elements": elements,
               "screenshot": str(shot) if shot else None,
               "wait_debug": adapter_common.WAIT_DEBUG.get(platform)}
        if probe:
            blocks = {}
            cls = ADAPTER_CLASSES.get(platform)
            mod = sys.modules.get(cls.__module__) if cls else None
            for kind, attr in (("blocks", "BLOCK_SELECTORS"), ("inputs", "INPUT_SELECTORS"),
                               ("send_btns", "SEND_BUTTON_SELECTORS")):
                sels = getattr(cls, attr, None) or getattr(mod, attr, None)
                if not sels:
                    continue
                for sel in sels:
                    try:
                        loc = page.locator(sel)
                        info = {"count": await loc.count()}
                        if info["count"]:
                            first = loc.first
                            info["visible"] = bool(await first.is_visible())
                            if kind == "blocks":
                                info["texts"] = [t[:200] for t in
                                                 (await loc.all_inner_texts())[:3]]
                            else:
                                try:
                                    info["value"] = (await adapter_common.read_input_value(first))[:60]
                                except Exception:
                                    info["value"] = None
                        blocks[f"{kind}::{sel}"] = info
                    except Exception as e:
                        blocks[f"{kind}::{sel}"] = {"error": str(e)[:120]}
            out["block_probe"] = blocks
        if not shot:
            out["screenshot_err"] = screenshot_err
        return out
    except Exception as e:
        return {"error": str(e)}


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")


if __name__ == "__main__":
    import uvicorn
    srv = CONFIG.get("server", {})
    uvicorn.run("app.main:app", host=srv.get("host", "127.0.0.1"),
                port=int(srv.get("port", 8787)))
