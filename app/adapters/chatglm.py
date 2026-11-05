"""智谱清言网页版适配器（M3）。

站点：https://chatglm.cn/
输入框：textarea#chat-input（备选：通用 textarea / contenteditable）
回复块：.chat-content-item-assistant .markdown-body（备选：.markdown-body 等）
未登录跳转 /auth → 提示人工登录（5 分钟），超时熔断。
"""
import asyncio

from .. import browser as bm
from .base import BaseAdapter
from . import common

BASE_URL = "https://chatglm.cn/"
INPUT_SELECTORS = (
    "textarea#chat-input",
    "div[contenteditable='true']",
    "textarea",
)
BLOCK_SELECTORS = (
    ".chat-content-item-assistant .markdown-body",
    ".markdown-body",
    "div[class*='markdown']",
)
LOGIN_HINTS = ("login", "auth")
# 仅匹配明确的平台级提示文案
RISK_TEXTS = ("请完成验证", "操作过于频繁", "请求过于频繁", "登录已过期", "异常流量")


class ChatGLMAdapter(BaseAdapter):
    name = "chatglm"
    platform_name = "智谱清言"

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
                "需要登录：请在打开的浏览器窗口中手动登录智谱清言（5 分钟内有效）"))
        if not ready:
            await on_error("智谱清言页面未就绪（登录超时或页面改版），渠道已暂停", fatal=True)
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
                                         on_delta, start_timeout=90, done_timeout=360,
                                         prev_texts=prev_texts,
                                         risk_fn=self._risk_detected,
                                         debug_key=self.platform_name)
        if result is None:
            await on_error("等待回复超时，请重试")
        elif isinstance(result, str) and result.startswith(common.RISK_PREFIX):
            await on_error(result[len(common.RISK_PREFIX):], fatal=True)
        else:
            await on_done()
