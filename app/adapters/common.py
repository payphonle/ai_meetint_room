"""适配器通用工具：就绪检测、拟人发送、DOM 回复轮询。真实平台共用。

防风控约定：
- 只读 DOM（inner_text），不抓包、不拦截网络
- 真实键盘输入 + 鼠标点击；发送成功以"输入框被清空"为准，最多补按 2 次 Enter
- 回复轮询只在"新块出现前"扫描平台级风控文案，避免把聊天内容误判
"""
import asyncio
import time

from .. import browser as bm

RISK_PREFIX = "__RISK__"

# 实时轮询诊断：debug_key -> 最近一次轮询状态快照（供 /api/debug/dom 查看）
WAIT_DEBUG: dict = {}


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


async def wait_reply(page, block_selectors, prev_counts: dict, on_delta, *,
                     start_timeout: float = 40, done_timeout: float = 300,
                     stable_secs: float = 2.5, risk_fn=None, debug_key: str | None = None):
    """轮询新增回复块：哪个选择器先出现新块就锁定它；文本 stable_secs 无变化视为完成。

    返回：回复文本 | None（超时）| "__RISK__<原因>"（熔断）。
    """
    t0 = time.monotonic()
    last, stable_since, last_src = "", None, None
    dbg = WAIT_DEBUG.setdefault(debug_key or "?", {})
    while True:
        now = time.monotonic()
        elapsed = now - t0
        if elapsed > done_timeout:
            return last or None
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
                count = await page.locator(sel).count()
            except Exception:
                continue
            if count <= prev_counts.get(sel, 0):
                continue
            progressed = True
            try:
                t = await page.locator(sel).nth(count - 1).inner_text()
            except Exception:
                t = last
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
