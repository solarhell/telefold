"""不可信终端文本的显示边界。"""

import pytest

from telefold.display import display_text


@pytest.mark.parametrize("value", ["普通中文", "空格 '引号' \\ 路径", "😀", "技术 / 工作"])
def test_printable_text_is_unchanged(value):
    """普通文字、引号、反斜杠和独立 emoji 不因转义而改变。"""
    assert display_text(value) == value


def test_invisible_and_control_characters_are_literal_text():
    """换行、回车、ANSI、双向控制字符及 ZWJ 都不能作为终端控制指令执行。"""
    assert display_text("一\n二\r\x1b[2J\u202e👩\u200d💻") == "一\\n二\\r\\x1b[2J\\u202e👩\\u200d💻"
