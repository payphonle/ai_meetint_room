"""探针：打印某平台各回复块选择器实际抓到的文本。用法: python probe_blocks.py 豆包"""
import io
import json
import sys
import urllib.parse
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

platform = sys.argv[1] if len(sys.argv) > 1 else "豆包"
url = ("http://127.0.0.1:8787/api/debug/dom/"
       + urllib.parse.quote(platform) + "?probe=1")
d = json.load(urllib.request.urlopen(url))
print("URL:", d.get("url"))
wd = d.get("wait_debug")
if wd:
    print("wait_debug:", json.dumps(wd, ensure_ascii=False))
bp = d.get("block_probe")
if not bp:
    print("no probe data:", d)
for sel, texts in (bp or {}).items():
    print("SEL:", sel)
    for t in texts:
        print("   >", json.dumps(t, ensure_ascii=False)[:300])
    print()
