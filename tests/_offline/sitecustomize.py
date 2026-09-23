"""Loaded automatically by every Python subprocess the test-suite starts.

tests/conftest.py puts this folder on PYTHONPATH for subprocesses, so a
script under test can only open sockets to loopback addresses. Any attempt
to reach the internet fails loudly instead of silently depending on it.
"""

import ipaddress
import socket

_LOCAL_NAMES = {"localhost", "localhost.localdomain", ""}


def _is_local(host) -> bool:
    if host is None:
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    host = str(host).strip("[]")
    if host.lower() in _LOCAL_NAMES:
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


class NetworkBlocked(OSError):
    pass


def _check(address) -> None:
    if isinstance(address, tuple) and address and not _is_local(address[0]):
        raise NetworkBlocked(f"test suite is offline: refused connection to {address[0]}")


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _connect(self, address):
    _check(address)
    return _real_connect(self, address)


def _connect_ex(self, address):
    _check(address)
    return _real_connect_ex(self, address)


def _getaddrinfo(host, *args, **kwargs):
    if not _is_local(host):
        raise socket.gaierror(f"test suite is offline: refused DNS lookup of {host}")
    return _real_getaddrinfo(host, *args, **kwargs)


def install() -> None:
    socket.socket.connect = _connect
    socket.socket.connect_ex = _connect_ex
    socket.getaddrinfo = _getaddrinfo


install()
