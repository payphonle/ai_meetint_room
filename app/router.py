"""@ 提及解析：@某人 → 仅该助手收到；不 @ 或 @所有人 → 全员收到。"""
import re

ALL_KEYWORDS = ("所有人", "全部", "大家")


def parse_mentions(text: str, member_names: list[str]) -> tuple[list[str], bool]:
    """返回 (targets, mentioned_all)。targets 为要投递的助手名列表。"""
    mentioned_all = any(f"@{kw}" in text for kw in ALL_KEYWORDS)
    targets = [name for name in member_names if f"@{name}" in text]
    if mentioned_all or not targets:
        return list(member_names), mentioned_all
    return targets, False


def strip_mentions(text: str, member_names: list[str]) -> str:
    """剥掉 @ 标记，得到投递给助手的干净正文（@ 只是路由指令，助手无需看到）。"""
    out = text
    for token in list(member_names) + list(ALL_KEYWORDS):
        out = out.replace(f"@{token}", "")
    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r"\n +", "\n", out)
    return out.strip()
