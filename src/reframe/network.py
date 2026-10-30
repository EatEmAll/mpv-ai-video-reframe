"""Process-local Python network denial; native libraries are outside this guard."""

import socket
import sys
from contextlib import contextmanager
from threading import local

from .security import SecurityError

_state = local()
_installed = False


def _audit(event, args):
    if getattr(_state, "setup", False):
        return
    if (
        event == "socket.getaddrinfo"
        or (event == "socket.__new__" and args[1] in {socket.AF_INET, socket.AF_INET6})
    ) or (
        event in {"socket.connect", "socket.bind", "socket.sendto", "socket.sendmsg"}
        and args[0].family in {socket.AF_INET, socket.AF_INET6}
    ):
        raise SecurityError("Dependency network access denied; use explicit setup")


def install_network_guard():
    global _installed
    if not _installed:
        sys.addaudithook(_audit)
        _installed = True


@contextmanager
def setup_network():
    previous = getattr(_state, "setup", False)
    _state.setup = True
    try:
        yield
    finally:
        _state.setup = previous
