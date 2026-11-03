"""M1 回声假助手：不联网，模拟流式回复，用于联调界面与队列全链路。"""
import asyncio
import random

from .base import BaseAdapter


class EchoAdapter(BaseAdapter):
    name = "echo"

    async def send(self, text: str, on_delta, on_done, on_error,
                   is_cancelled=None, on_cancel=None):
        reply = f"【M1 回声测试】收到：{text}"
        content = ""
        for ch in reply:
            content += ch
            await on_delta(content)
            await asyncio.sleep(random.uniform(0.01, 0.04))
        await on_done()
