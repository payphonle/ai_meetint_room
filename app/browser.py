"""浏览器管理：每平台独立持久化 Chrome，标签页常驻整个运行期，退出时统一关闭。

防风控要点：
- channel="chrome"：使用系统真实 Chrome（指纹与日常一致），缺失时依次回退 msedge / 自带 chromium
- 有头模式 + 独立 user_data_dir（cookie/登录态落盘，登录一次长期有效）
- 去除自动化标记（AutomationChromium / enable-automation），使特征与正常浏览器一致
- 不做定时保活、不拦截网络、不注入指纹伪造脚本
"""
import asyncio
import random
from pathlib import Path

from playwright.async_api import async_playwright

# 依次回退的浏览器通道；None = playwright 自带 chromium
CHANNELS = ("chrome", "msedge", None)


async def human_pause(lo: float = 0.3, hi: float = 1.2):
    await asyncio.sleep(random.uniform(lo, hi))


async def human_click(page, locator, timeout_ms: int = 5000):
    """带随机偏移的鼠标移动+点击，模拟真人。

    所有等待均限时（默认 5 秒）：目标元素可能在 SPA 水合中被替换，
    绝不能让点击挂在 30 秒默认超时上。失败由调用方重挑元素重试。
    """
    try:
        box = await locator.bounding_box(timeout=timeout_ms)
    except Exception:
        box = None
    if box:
        x = box["x"] + box["width"] / 2 + random.uniform(-4, 4)
        y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
        await page.mouse.move(x - random.uniform(15, 50), y - random.uniform(10, 40))
        await human_pause(0.08, 0.3)
        await page.mouse.move(x, y, steps=random.randint(5, 12))
        await human_pause(0.05, 0.18)
        await page.mouse.click(x, y)
    else:
        await locator.click(timeout=timeout_ms)


async def human_type(page, text: str, long_text_threshold: int = 120):
    """真实键盘逐字输入，随机字间延迟 + 偶发停顿；长文本自动加速（像粘贴后补打）。

    换行用 Shift+Enter（不触发发送）。
    """
    for i, ch in enumerate(text):
        if ch == "\n":
            await page.keyboard.press("Shift+Enter")
        else:
            await page.keyboard.type(ch)
        base = random.uniform(1 / 180, 1 / 60)
        if len(text) > long_text_threshold and i > 40:
            base *= 0.35
        await asyncio.sleep(base)
        if random.random() < 0.03:
            await asyncio.sleep(random.uniform(0.3, 0.9))


class BrowserManager:
    def __init__(self, profiles_dir: str):
        self.profiles_dir = Path(profiles_dir)
        self._pw = None
        self._contexts: dict[str, object] = {}
        self._pages: dict[str, object] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def _ensure_pw(self):
        if self._pw is None:
            self._pw = await async_playwright().start()
        return self._pw

    async def get_page(self, platform: str, url: str, url_prefix: str | None = None):
        """打开（或复用）平台常驻标签页。首次打开后一直保留，不自动关闭。"""
        prefix = url_prefix or url
        page = self._pages.get(platform)
        if page is not None and not page.is_closed():
            if not page.url.startswith(prefix):
                await page.goto(url, wait_until="domcontentloaded")
            return page

        pw = await self._ensure_pw()
        profile = self.profiles_dir / platform
        profile.mkdir(parents=True, exist_ok=True)
        lock = self._locks.setdefault(platform, asyncio.Lock())
        async with lock:
            page = self._pages.get(platform)
            if page is not None and not page.is_closed():
                if not page.url.startswith(prefix):
                    await page.goto(url, wait_until="domcontentloaded")
                return page

            context, last_err = None, None
            for channel in CHANNELS:
                kwargs = dict(
                    user_data_dir=str(profile),
                    headless=False,          # 红线：永远有头
                    viewport=None,           # 跟随真实窗口尺寸
                    locale="zh-CN",
                    timezone_id="Asia/Shanghai",
                    args=[
                        "--disable-blink-features=AutomationChromium",
                        "--start-maximized",
                    ],
                )
                if channel:
                    kwargs["channel"] = channel
                try:
                    context = await pw.chromium.launch_persistent_context(**kwargs)
                    break
                except Exception as e:
                    last_err = e
            if context is None:
                raise RuntimeError(f"无法启动浏览器：{last_err}")

            page = context.pages[0] if context.pages else await context.new_page()
            if not page.url.startswith(prefix):
                await page.goto(url, wait_until="domcontentloaded")
            self._contexts[platform] = context
            self._pages[platform] = page
            return page

    async def close_all(self):
        for ctx in self._contexts.values():
            try:
                await ctx.close()
            except Exception:
                pass
        self._contexts.clear()
        self._pages.clear()
        if self._pw:
            try:
                await self._pw.stop()
            except Exception:
                pass
            self._pw = None
