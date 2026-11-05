"""DeepSeek 网页版适配器（M2，M4 重构为共用框架）。

站点：https://chat.deepseek.com/
输入框：2026 改版后 textarea 无 id，改用 placeholder 匹配（更稳定）+ 通用回退
回复块：.ds-markdown（备选 .markdown-body / 通用 markdown 容器）
发送：Enter（以"输入框清空"确认成功，最多补按 2 次）

熔断条件（fatal）：需要登录且超时 / 输入框不存在（页面改版）/ 明确的风控提示。
"""
import asyncio

from .. import browser as bm
from .base import BaseAdapter
from . import common

BASE_URL = "https://chat.deepseek.com/"
INPUT_SELECTORS = (
    "textarea[placeholder*='DeepSeek']",
    "textarea[placeholder*='发送消息']",
    "div[contenteditable='true']",
    "textarea",
)
BLOCK_SELECTORS = (
    ".ds-markdown",
    ".markdown-body",
    "div[class*='markdown']",
)
LOGIN_HINTS = ("login", "sign_in")
# 仅匹配明确的平台级提示文案（避免把聊天内容里的普通词误判为风控）
RISK_TEXTS = ("请完成验证", "操作过于频繁", "请求过于频繁", "登录已过期", "异常流量")
STABLE_SECS = 2.5


class DeepSeekAdapter(BaseAdapter):
    name = "deepseek"
    platform_name = "DeepSeek"

    def __init__(self, config=None, browser_manager=None, on_info=None):
        super().__init__(config)
        self.bm = browser_manager
        self._on_info = on_info or (lambda platform, text: None)

    def _info(self, text: str):
        self._on_info(self.platform_name, text)

    async def _risk_detected(self, page) -> bool:
        try:
            for t in RISK_TEXTS:
                if await page.get_by_text(t, exact=False).count() > 0:
                    return True
        except Exception:
            pass
        return False

    async def send(self, text: str, on_delta, on_done, on_error,
                   is_cancelled=None, on_cancel=None):
        try:
            page = await self.bm.get_page(self.platform_name, BASE_URL)
        except Exception as e:
            await on_error(f"浏览器启动失败：{e}", fatal=True)
            return

        ready = await common.wait_input_ready(
            page, INPUT_SELECTORS, LOGIN_HINTS, timeout=320,
            on_need_login=lambda: self._info(
                "需要登录：请在打开的浏览器窗口中手动登录 DeepSeek（5 分钟内有效）"))
        if not ready:
            await on_error("DeepSeek 页面未就绪（登录超时或页面改版），渠道已暂停", fatal=True)
            return

        try:
            prev_counts = await common.snapshot_block_counts(page, BLOCK_SELECTORS)
            prev_texts = await common.snapshot_block_texts(page, BLOCK_SELECTORS)
        except Exception:
            prev_counts = {sel: 0 for sel in BLOCK_SELECTORS}
            prev_texts = []
        pre_url = page.url

        try:
            res = await common.type_and_send(page, INPUT_SELECTORS, text,
                                             pre_url=pre_url,
                                             block_selectors=BLOCK_SELECTORS,
                                             prev_counts=prev_counts,
                                             is_cancelled=is_cancelled,
                                             on_cancel=on_cancel)
        except RuntimeError as e:
            await on_error(str(e), fatal=True)
            return
        if res == "cancelled":
            return
        if res != "sent":
            await on_error("消息未能发出，请重试")
            return

        await asyncio.sleep(1)  # 等 UI 开始渲染再进入轮询
        result = await common.wait_reply(page, BLOCK_SELECTORS, prev_counts,
                                         on_delta, stable_secs=STABLE_SECS,
                                         start_timeout=120, done_timeout=360,
                                         prev_texts=prev_texts,
                                         risk_fn=self._risk_detected,
                                         debug_key=self.platform_name)
        if result is None:
            await on_error("等待回复超时，请重试")
        elif isinstance(result, str) and result.startswith(common.RISK_PREFIX):
            await on_error(result[len(common.RISK_PREFIX):], fatal=True)
        else:
            await on_done()
