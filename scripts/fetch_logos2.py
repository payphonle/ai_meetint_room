"""从各助手官网首页解析真实 favicon/logo 地址并下载到 web/logos/。"""
import io
import re
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

SITES = {
    "doubao": "https://www.doubao.com/",
    "deepseek": "https://chat.deepseek.com/",
    "qianwen": "https://www.qianwen.com/",
    "chatglm": "https://chatglm.cn/",
    "yuanbao": "https://yuanbao.tencent.com/",
}
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9",
}


def fetch(url, timeout=20):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def is_image(data: bytes) -> bool:
    return (data[:8] == b"\x89PNG\r\n\x1a\n" or data[:4] == b"\x00\x00\x01\x00"
            or data[:3] == b"\xff\xd8\xff" or data[:4] == b"GIF8")


def extract_icons(html: str):
    """返回 (href, priority) 列表：apple-touch 优先，sizes 大者优先。"""
    out = []
    for m in re.finditer(r"<link[^>]+>", html, re.I):
        tag = m.group(0)
        rel = re.search(r'rel=["\']([^"\']*)["\']', tag, re.I)
        href = re.search(r'href=["\']([^"\']+)["\']', tag, re.I)
        sizes = re.search(r'sizes=["\']([^"\']*)["\']', tag, re.I)
        if not rel or not href:
            continue
        rel_l = rel.group(1).lower()
        if "icon" not in rel_l:
            continue
        pri = 0
        if "apple-touch" in rel_l or "shortcut" in rel_l:
            pri += 2
        if sizes:
            try:
                w = int(sizes.group(1).lower().split("x")[0])
                pri += min(w // 64, 4)
            except ValueError:
                pass
        out.append((href.group(1), pri))
    out.sort(key=lambda x: -x[1])
    return out


for key, home in SITES.items():
    saved = False
    try:
        html = fetch(home).decode("utf-8", errors="ignore")
        import urllib.parse
        for href, _pri in extract_icons(html):
            url = urllib.parse.urljoin(home, href)
            if not url.startswith("http"):
                continue
            try:
                data = fetch(url)
            except Exception:
                continue
            if is_image(data) and len(data) > 800:
                open(rf"web\logos\{key}.png", "wb").write(data)
                print(f"{key}: {url} ({len(data)//1024}KB)")
                saved = True
                break
    except Exception as e:
        print(f"{key}: homepage error {e}")
    if not saved:
        print(f"{key}: FAILED（前端将回退为字母头像）")
