"""Verify the actual connected transport before the IPC library sends commands."""

import os
from threading import Lock

import python_mpv_jsonipc as library

from .security import SecurityError, validate_endpoint

_creation = Lock()


def connect_mpv(endpoint, *, player_pid=None, **kwargs):
    endpoint = validate_endpoint(endpoint, player_pid=player_pid)
    name = "WindowsSocket" if os.name == "nt" else "UnixSocket"
    with _creation:
        original = getattr(library, name)

        class VerifiedSocket(original):
            def start(self):
                try:
                    if os.name == "nt":
                        from .windows_security import verify_pipe_handle

                        verify_pipe_handle(self._pipe, player_pid=player_pid)
                    else:
                        # Check after connect too. The private parent prevents
                        # other users replacing the endpoint between checks.
                        validate_endpoint(endpoint)
                        import socket
                        import struct

                        if hasattr(socket, "SO_PEERCRED"):
                            pid, uid, _ = struct.unpack(
                                "3i",
                                self.socket.getsockopt(
                                    socket.SOL_SOCKET, socket.SO_PEERCRED, 12
                                ),
                            )
                            if uid != os.getuid() or (
                                player_pid is not None and pid != player_pid
                            ):
                                raise SecurityError("Unexpected Unix IPC peer")
                        elif hasattr(self.socket, "getpeereid"):
                            uid, _ = self.socket.getpeereid()
                            if uid != os.getuid():
                                raise SecurityError("Unexpected Unix IPC peer")
                        else:
                            raise SecurityError(
                                "Native Unix peer credential API unavailable"
                            )
                except BaseException:
                    self.stop(join=False)
                    raise
                super().start()

        # The pinned library selects its socket class in MPVInter.__init__.
        # Serialize construction, restore immediately, and retain normal teardown.
        setattr(library, name, VerifiedSocket)
        try:
            return library.MPV(start_mpv=False, ipc_socket=endpoint, **kwargs)
        finally:
            setattr(library, name, original)
