"""豆包登录后端到端诊断：发一条消息，追踪状态流转，最后输出页面探针数据。"""
import io
import json
import sys
import time
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
BASE = "http://127.0.0.1:8787"


def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method)
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json; charset=utf-8")
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    with urllib.request.urlopen(req, data, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


text = "用一句话介绍你自己"
api("/api/send", "POST", {"text": f"@豆包 {text}", "conv_type": "group"})
print(f"sent: {text}")

last = None
start = time.time()
final_msg = None
while time.time() - start < 420:  # 最长 7 分钟
    time.sleep(10)
    msgs = api("/api/messages?conv=group")["messages"]
    mine = [m for m in msgs if m["sender"] == "me" and m["content"].endswith(text)]
    if not mine:
        continue
    user_msg = mine[-1]
    replies = [m for m in msgs if m["sender"] == "豆包" and m["id"] > user_msg["id"]]
    status = user_msg["status"]
    reply = replies[0] if replies else None
    snap = (status, reply["status"] if reply else "-", len(reply["content"] or "") if reply else 0)
    if snap != last:
        print(f"[{int(time.time()-start):>3}s] user_msg={status}, reply={snap[1]}, len={snap[2]}")
        last = snap
    if status in ("done", "failed", "cancelled") or (reply and reply["status"] in ("done", "failed")):
        final_msg = (user_msg, reply)
        break

user_msg, reply = final_msg if final_msg else (None, None)
if user_msg:
    print(f"FINAL: user_msg={user_msg['status']}")
if reply:
    print(f"FINAL: reply={reply['status']} content[:120]={ (reply['content'] or '')[:120]!r}")

d = api("/api/debug/dom/" + urllib.parse.quote("豆包") + "?probe=1")
print("PAGE URL:", d.get("url"))
print("wait_debug:", json.dumps(d.get("wait_debug"), ensure_ascii=False))
for k, v in (d.get("block_probe") or {}).items():
    print(k[:80], "=>", json.dumps(v, ensure_ascii=False)[:220])
