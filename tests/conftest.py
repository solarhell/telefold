"""所有回归测试均禁止真实网络访问。"""

import socket

import pytest


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("测试不得连接真实 Telegram、LLM 或其他网络服务")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
