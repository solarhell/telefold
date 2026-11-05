"""所有回归测试均禁止真实网络访问。"""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """补齐所有 DNS 入口；互联网 socket 创建由 pytest-socket 统一禁用。"""

    def forbidden(*args, **kwargs):
        pytest.fail("测试不得连接真实 Telegram、LLM 或其他网络服务")

    for name in (
        "getaddrinfo",
        "gethostbyname",
        "gethostbyname_ex",
        "gethostbyaddr",
        "getnameinfo",
    ):
        monkeypatch.setattr(socket, name, forbidden)
