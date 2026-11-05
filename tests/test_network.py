"""验证测试隔离边界，回归用例本身不发送任何真实网络请求。"""

import socket

import pytest
from pytest_socket import SocketBlockedError


@pytest.mark.parametrize("family", [socket.AF_INET, socket.AF_INET6])
@pytest.mark.parametrize("kind", [socket.SOCK_STREAM, socket.SOCK_DGRAM])
def test_internet_sockets_are_blocked_at_creation(family, kind):
    """TCP 和 UDP 在创建时就被拦截，无连接的 sendto 也拿不到网络 socket。"""
    with pytest.warns(UserWarning), pytest.raises(SocketBlockedError):
        with socket.socket(family, kind):
            pass


@pytest.mark.parametrize(
    "name,args",
    [
        ("getaddrinfo", (object(), None)),
        ("gethostbyname", (object(),)),
        ("gethostbyname_ex", (object(),)),
        ("gethostbyaddr", (object(),)),
        ("getnameinfo", (object(), 0)),
    ],
)
def test_resolver_entry_points_are_blocked(name, args):
    """所有解析入口都先命中测试保护；错误类型参数确保保护退化时也不会查询网络。"""
    with pytest.raises(pytest.fail.Exception, match="测试不得连接"):
        getattr(socket, name)(*args)


def test_standard_socketpair_remains_local_and_usable():
    """事件循环的本地成对 socket 能通信并保持类型身份，不能因此创建其他 IP socket。"""
    left, right = socket.socketpair()
    try:
        assert isinstance(left, socket.socket)
        assert isinstance(right, socket.socket)
        left.settimeout(1)
        right.settimeout(1)
        left.sendall(b"wake")
        assert right.recv(4) == b"wake"
        if left.family in (socket.AF_INET, socket.AF_INET6):
            assert left.getpeername()[0] in {"127.0.0.1", "::1"}
        with pytest.warns(UserWarning), pytest.raises(SocketBlockedError):
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM):
                pass
    finally:
        left.close()
        right.close()


def test_failed_socketpair_does_not_leave_network_access_open():
    """标准库调用参数错误时，临时创建窗口也必须恢复为禁止 IP socket。"""
    with pytest.raises(TypeError):
        socket.socketpair(invalid_argument=True)
    with pytest.warns(UserWarning), pytest.raises(SocketBlockedError):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM):
            pass
