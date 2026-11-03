"""适配器基类：真实浏览器适配器（DeepSeek/豆包/千问/智谱清言/元宝）实现此接口。"""


class BaseAdapter:
    name = "base"
    platform_name = "base"

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    async def send(self, text: str, on_delta, on_done, on_error,
                   is_cancelled=None, on_cancel=None):
        """发送一条消息并等待回复。

        on_delta(content)               流式增量回调（content 为累计内容）
        on_done()                       回复完成
        on_error(err, fatal=False)      出错；fatal=True 表示触发熔断
                                        （登录失效/验证码/风控提示/页面改版）
        is_cancelled() -> bool          取消检查（发送前多次调用）
        on_cancel()                     已在发送前取消（调用方负责状态更新）
        实现要求（防风控红线）：
        - 只读 DOM，不抓包、不拦截网络帧
        - 真实键盘逐字输入、真实鼠标点击
        - 复用常驻标签页，不刷新、不反复导航
        - 遇到验证码/登录页/频繁提示调用 on_error(fatal=True)，绝不重试绕过
        """
        raise NotImplementedError
