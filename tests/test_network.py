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
