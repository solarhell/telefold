"""所有回归测试均禁止真实网络访问。"""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """在测试期间拦截 DNS 解析及套接字连接，避免任何真实网络请求。"""

    def forbidden(*args, **kwargs):
        pytest.fail("测试不得连接真实 Telegram、LLM 或其他网络服务")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
