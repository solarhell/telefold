"""所有回归测试均禁止真实网络访问。"""

import socket
from contextvars import ContextVar

import pytest

# 在 pytest-socket 的逐测试保护启用前保存标准库类型。
_SOCKET_CLASS = socket.socket


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    """保留 socket 禁用和 DNS 隔离，仅为标准库本地 socketpair 开放创建窗口。"""
    pair_active = ContextVar("local_socketpair", default=False)
    guarded_socket = socket.socket
    original_pair = socket.socketpair

    class PairSocket(guarded_socket):
        """仅在标准库 socketpair 内创建 TCP socket，其他情况沿用插件保护。"""

        def __new__(cls, family=-1, type=-1, proto=-1, fileno=None):
            """支持 Windows 的回环 TCP 自唤醒管道，保持 asyncio 的 socket 类型检查。"""
            if (
                pair_active.get()
                and family in (socket.AF_INET, socket.AF_INET6)
                and type == socket.SOCK_STREAM
            ):
                return _SOCKET_CLASS.__new__(cls, family, type, proto, fileno)
            return super().__new__(cls, family, type, proto, fileno)

    def local_socketpair(*args, **kwargs):
        """只包装标准库的本地成对连接实现，成功或失败后都关闭临时创建窗口。"""
        token = pair_active.set(True)
        try:
            return original_pair(*args, **kwargs)
        finally:
            pair_active.reset(token)

    monkeypatch.setattr(socket, "socket", PairSocket)
    monkeypatch.setattr(socket, "socketpair", local_socketpair)

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
