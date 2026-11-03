"""验证：取消后队列排空、豆包未消耗当日额度。"""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
d = json.load(urllib.request.urlopen("http://127.0.0.1:8787/api/platforms"))
for p in d["platforms"]:
    print(f"{p['name']}: queue={p['queue_len']} sent={p['sent_today']}/{p['daily_cap']} paused={p['paused']}")
