"""豆包网页版适配器（M4 · 严格档）。

站点：https://www.doubao.com/chat/
输入框：textarea[data-testid='chat_input_input']（备选 contenteditable / textarea）
发送：优先点击 data-testid='chat_input_send_button'，回退 Enter
回复块：div[data-testid='receive_message']（仅助手消息，安全）内的 markdown

严格档防风控：间隔 90~300 秒、日限 20 条（config.yaml）；只读 DOM；
登录超时 / 页面改版 / 明确风控文案 → 熔断，零重试。建议使用独立小号。
"""
import asyncio

from .. import browser as bm
from .base import BaseAdapter
from . import common

BASE_URL = "https://www.doubao.com/chat/"
INPUT_SELECTORS = (
    # 豆包输入框是 tiptap/ProseMirror 编辑器（水合后稳定存在）；加载期可能瞬间
    # 出现 textarea 变体（幽灵元素），故 ProseMirror 优先，textarea 仅兜底
    "div[contenteditable='true'].ProseMirror",
    "textarea[data-testid='chat_input_input']",
    "div[contenteditable='true']",
    "textarea",
)
SEND_BUTTON_SELECTORS = (
    "button[data-testid='chat_input_send_button']",
    "div[data-testid='chat_input_send_button']",
)
BLOCK_SELECTORS = (
    # 精确正文节点（推荐追问/下载横幅/搜索状态行都在它外面）
    "div[data-testid='receive_message'] div[data-testid='message_text_content']",
    # 整块兜底（会带推荐追问等噪音）
    "div[data-testid='receive_message']",
)
LOGIN_HINTS = ("login", "passport")
RISK_TEXTS = ("请完成验证", "操作过于频繁", "请求过于频繁", "登录已过期",
              "异常流量", "存在安全风险")
STABLE_SECS = 3.0  # 严格档：更保守的稳定判定


class DoubaoAdapter(BaseAdapter):
    name = "doubao"
    platform_name = "豆包"

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
                "需要登录：请在打开的浏览器窗口中手动登录豆包（5 分钟内有效）"))
        if not ready:
            await on_error("豆包页面未就绪（登录超时或页面改版），渠道已暂停", fatal=True)
            return

        try:
            prev_counts = await common.snapshot_block_counts(page, BLOCK_SELECTORS)
        except Exception:
            prev_counts = {sel: 0 for sel in BLOCK_SELECTORS}
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
                                         risk_fn=self._risk_detected,
                                         debug_key=self.platform_name)
        if result is None:
            await on_error("等待回复超时，请重试")
        elif isinstance(result, str) and result.startswith(common.RISK_PREFIX):
            await on_error(result[len(common.RISK_PREFIX):], fatal=True)
        else:
            await on_done()
