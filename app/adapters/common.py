"""适配器通用工具：就绪检测、拟人发送、DOM 回复轮询。真实平台共用。

防风控约定：
- 只读 DOM（inner_text），不抓包、不拦截网络
- 真实键盘输入 + 鼠标点击；发送成功以"输入框被清空"为准，最多补按 2 次 Enter
- 回复轮询只在"新块出现前"扫描平台级风控文案，避免把聊天内容误判
"""
import asyncio
import re
import time

from .. import browser as bm

RISK_PREFIX = "__RISK__"

# 实时轮询诊断：debug_key -> 最近一次轮询状态快照（供 /api/debug/dom 查看）
WAIT_DEBUG: dict = {}

# 平台把列表符号渲染成独立块时，inner_text 会得到 "•\n正文"；合并回 "• 正文"
_BULLET_ONLY_LINE = re.compile(r"(?m)^[ \t]*([•·▪◦‣])[ \t]*\n[ \t]*(?=\S)")


def normalize_block_text(t: str) -> str:
    """规整从 DOM 取到的回复文本：统一换行、合并孤立列表符号。"""
    if not t:
        return t
    t = t.replace("\r\n", "\n").replace("\r", "\n")
    t = _BULLET_ONLY_LINE.sub(r"\1 ", t)
    return t.strip()



async def pick_visible(page, selectors):
    """返回第一个可见元素，找不到返回 None。"""
    for sel in selectors:
        try:
            loc = page.locator(sel).first
            if await loc.is_visible():
                return loc
        except Exception:
            continue
    return None


async def read_input_value(loc) -> str:
    """兼容 textarea（.value）与 contenteditable（.innerText）。"""
    try:
        return await loc.evaluate(
            "el => el.value !== undefined ? el.value : (el.innerText || '')")
    except Exception:
        return ""


async def wait_input_ready(page, input_selectors, login_hints, timeout: float,
                           on_need_login=None) -> bool:
    """等待输入框就绪；检测到登录页则提示人工登录（限时）。"""
    end = time.monotonic() + timeout
    login_hinted = False
    while time.monotonic() < end:
        url = page.url.lower()
        if any(h in url for h in login_hints):
            if on_need_login and not login_hinted:
                login_hinted = True
                on_need_login()
            await asyncio.sleep(1)
            continue
        if await pick_visible(page, input_selectors):
            return True
        await asyncio.sleep(1)
    return False


async def clear_input(page, input_selectors):
    """取消时清空已输入的草稿（点击输入框 → Ctrl+A → Backspace），不留痕迹。"""
    target = await pick_visible(page, input_selectors)
    if target is None:
        return
    try:
        await bm.human_click(page, target, timeout_ms=3000)
        await bm.human_pause(0.1, 0.3)
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
    except Exception:
        pass


async def type_and_send(page, input_selectors, text: str, confirm_attempts: int = 3,
                        send_button_selectors=None, pre_url: str | None = None,
                        block_selectors=None, prev_counts: dict | None = None,
                        is_cancelled=None, on_cancel=None) -> str:
    """拟人输入并发送。返回 "sent" | "failed" | "cancelled"。

    发送成功判定（任一满足即可）：
    1. 输入框被清空（主证据）
    2. URL 发生变化（如新建会话后进入 /chat/<id>）
    3. 出现了新的回复块（block 计数增长）
    取消：is_cancelled 回调在点击前/输入前/发送前多次检查；取消时清空草稿
    并调用 on_cancel，保证平台上不留任何已输入内容。
    """
    async def _aborted_if_cancelled() -> bool:
        if is_cancelled is not None and await is_cancelled():
            await clear_input(page, input_selectors)
            if on_cancel is not None:
                await on_cancel()
            return True
        return False

    target = await pick_visible(page, input_selectors)
    if target is None:
        raise RuntimeError("未找到输入框（页面可能已改版）")
    if await _aborted_if_cancelled():
        return "cancelled"
    clicked = False
    for _ in range(3):
        target = await pick_visible(page, input_selectors)
        if target is None:
            raise RuntimeError("未找到输入框（页面可能已改版）")
        try:
            await bm.human_click(page, target, timeout_ms=5000)
            clicked = True
            break
        except Exception:
            await bm.human_pause(0.5, 1.0)
    if not clicked:
        raise RuntimeError("输入框点击失败（页面可能仍在加载或已改版）")
    await bm.human_pause(0.2, 0.6)
    if await _aborted_if_cancelled():
        return "cancelled"
    await bm.human_type(page, text)
    await bm.human_pause(0.4, 1.2)
    if await _aborted_if_cancelled():  # 关键窗口：已打完字、尚未发送
        return "cancelled"
    for _ in range(confirm_attempts):
        if await _aborted_if_cancelled():
            return "cancelled"
        clicked_btn = False
        for sel in (send_button_selectors or ()):
            try:
                btn = page.locator(sel).first
                if await btn.is_visible() and await btn.is_enabled():
                    await bm.human_click(page, btn, timeout_ms=5000)
                    clicked_btn = True
                    break
            except Exception:
                continue
        if not clicked_btn:
            await page.keyboard.press("Enter")
        for _ in range(10):  # 每次尝试给 5 秒确认窗口
            await asyncio.sleep(0.5)
            target = await pick_visible(page, input_selectors)
            if target is None or not (await read_input_value(target)).strip():
                return "sent"
            if pre_url and page.url != pre_url:
                return "sent"
            if block_selectors and prev_counts:
                try:
                    for sel, pc in prev_counts.items():
                        if await page.locator(sel).count() > pc:
                            return "sent"
                except Exception:
                    pass
    return "failed"


async def snapshot_block_counts(page, block_selectors) -> dict:
    counts = {}
    for sel in block_selectors:
        try:
            counts[sel] = await page.locator(sel).count()
        except Exception:
            counts[sel] = 0
    return counts


async def snapshot_block_texts(page, block_selectors) -> list:
    """发送前的回复块文本快照，用于识别"新回复"。

    现代平台（豆包等）的消息列表是虚拟列表：新回复挂载时可能同时卸载旧节点，
    块计数不会增长，因此不能只靠 count 判断，必须比对文本集合。
    """
    texts = []
    for sel in block_selectors:
        try:
            texts.extend(await page.locator(sel).all_inner_texts())
        except Exception:
            continue
    return [normalize_block_text(t) for t in texts if t and t.strip()]


async def scroll_reply_area(page):
    """把消息列表滚到底部。

    虚拟列表只挂载视口附近的节点：页面没停在底部时，新回复可能压根不进 DOM，
    轮询就会一直看不到。这里兼容各平台常见的滚动容器写法。
    """
    try:
        await page.evaluate(
            """() => {
                const seeds = ["[data-testid='message-list']",
                               "[data-testid='message-list-container']",
                               "[class*='message-list']", "[class*='chat-list']"];
                let el = null;
                for (const s of seeds) {
                    el = document.querySelector(s);
                    if (el) break;
                }
                if (!el) el = document.scrollingElement || document.body;
                let node = el;
                while (node && node !== document.body) {
                    const st = getComputedStyle(node);
                    if (/(auto|scroll)/.test(st.overflowY)
                        && node.scrollHeight > node.clientHeight + 4) {
                        node.scrollTop = node.scrollHeight;
                        return;
                    }
                    node = node.parentElement;
                }
                if (el.scrollHeight !== undefined) el.scrollTop = el.scrollHeight;
                window.scrollTo(0, document.body.scrollHeight);
            }""")
    except Exception:
        pass


async def wait_reply(page, block_selectors, prev_counts: dict, on_delta, *,
                     start_timeout: float = 40, done_timeout: float = 300,
                     stable_secs: float = 2.5, risk_fn=None, debug_key: str | None = None,
                     prev_texts: list | None = None):
    """轮询新增回复块：哪个选择器先出现新块就锁定它；文本 stable_secs 无变化视为完成。

    新块判定 = 块计数增长 或 出现发送前快照里没有的文本。
    后者用于虚拟列表平台（豆包等）：新回复挂载时旧节点会被卸载，计数可能不增。

    返回：回复文本 | None（超时）| "__RISK__<原因>"（熔断）。
    """
    t0 = time.monotonic()
    last, stable_since, last_src = "", None, None
    prev_set = {t.strip() for t in (prev_texts or []) if t and t.strip()}
    dbg = WAIT_DEBUG.setdefault(debug_key or "?", {})
    last_scroll = 0.0
    while True:
        now = time.monotonic()
        elapsed = now - t0
        if elapsed > done_timeout:
            return last or None
        if now - last_scroll >= 3.0:  # 周期性滚到底，保证虚拟列表挂载最新回复
            last_scroll = now
            await scroll_reply_area(page)
        if debug_key:
            try:
                live = {s: await page.locator(s).count() for s in block_selectors}
                dbg.update(elapsed=round(elapsed, 1), src=last_src,
                           last_len=len(last), counts=live)
            except Exception as e:
                dbg.update(err=str(e)[:120], elapsed=round(elapsed, 1))
        progressed = False
        # 不做永久锁定：每轮按优先级（精确在前）选第一个有新块的；
        # 流式阶段精确节点可能尚未渲染（先抓外层块），完成态出现后自动切换到精确正文。
        # 非首选来源需稳定 30 秒才采信（等精确节点出现），首选来源按 stable_secs 稳定即完成
        for idx, sel in enumerate(block_selectors):
            try:
                loc = page.locator(sel)
                count = await loc.count()
            except Exception:
                continue
            if count == 0:
                continue
            try:
                texts = [normalize_block_text(x)
                         for x in await loc.all_inner_texts()]
            except Exception:
                texts = []
            t = None
            if count > prev_counts.get(sel, 0):
                t = texts[-1] if texts else last
            if not t:
                # 计数没涨（虚拟列表卸载了旧节点）：找快照里没有的最新文本
                for cand in reversed(texts):
                    if cand and cand.strip() and cand.strip() not in prev_set:
                        t = cand
                        break
            if not t:
                continue
            progressed = True
            need_stable = stable_secs if idx == 0 else 30.0
            if t and t != last:
                last = t
                last_src = sel
                stable_since = None
                await on_delta(t)
            elif last:
                if stable_since is None:
                    stable_since = now
                elif now - stable_since >= need_stable:
                    return last
            break
        if not progressed and not last and elapsed > start_timeout:
            if risk_fn and await risk_fn(page):
                return f"{RISK_PREFIX}出现风控/验证提示，渠道已暂停"
            return None
        await asyncio.sleep(0.3)
