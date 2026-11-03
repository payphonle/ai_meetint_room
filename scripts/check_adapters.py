"""校验五个适配器的取消参数、三态处理与超时配置。"""
import io
import re
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

for f in ("deepseek", "chatglm", "qianwen", "doubao", "yuanbao"):
    c = open(rf"app\adapters\{f}.py", encoding="utf-8").read()
    call = "is_cancelled=is_cancelled" in c and "res = await common.type_and_send" in c
    cancel = 'if res == "cancelled"' in c
    sig = "is_cancelled=None, on_cancel=None" in c
    to = re.search(r"start_timeout=(\d+), done_timeout=(\d+)", c)
    print(f"{f}: call_kwargs={call} cancel_handling={cancel} sig={sig} "
          f"timeouts={to.groups() if to else None} leftover_ok={'if not ok:' in c}")
