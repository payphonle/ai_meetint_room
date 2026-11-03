"""一次性清理：把卡在中间状态的消息标为 failed。"""
import sqlite3

con = sqlite3.connect(r"C:\Users\杨峰\ai-group-chat\data\chat.db")
cur = con.execute(
    "UPDATE messages SET status='failed' "
    "WHERE status IN ('typing','streaming','queued')")
con.commit()
print(f"updated rows: {cur.rowcount}")
