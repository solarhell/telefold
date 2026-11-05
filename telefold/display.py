"""将不可信文本安全地显示在终端中。"""


def display_text(value: object) -> str:
    """保留可打印字符，转义会改变终端布局的控制字符和不可见格式字符。"""
    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in str(value))
