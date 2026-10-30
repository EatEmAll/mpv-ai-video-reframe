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
            def __init__(self, *args, **options):
                if os.name != "nt":
                    super().__init__(*args, **options)
                    return
                # Scope a module proxy to construction; never mutate the shared
                # ctypes DLL function or open an impersonation-capable handle.
                native = library._kernel32

                class IdentificationAPI:
                    def __getattr__(self, name):
                        return getattr(native, name)

                    def CreateFileW(
                        self,
                        path,
                        access,
                        share,
                        attributes,
                        disposition,
                        flags,
                        template,
                    ):
                        from .windows_security import open_pipe_identification

                        return open_pipe_identification(
                            path,
                            access,
                            share,
                            attributes,
                            disposition,
                            flags,
                            template,
                        )

                library._kernel32 = IdentificationAPI()
                try:
                    super().__init__(*args, **options)
                finally:
                    library._kernel32 = native

            def start(self):
                try:
                    if os.name == "nt":
                        from .windows_security import verify_pipe_handle

                        verify_pipe_handle(self._pipe, player_pid=player_pid)
                    else:
                        # Check after connect too. The private parent prevents
                        # other users replacing the endpoint between checks.
                        validate_endpoint(endpoint)
                        verify_unix_peer(self.socket, player_pid=player_pid)
                except BaseException:
                    self.stop(join=False)
                    if os.name == "nt":
                        self._close()
                    raise
                super().start()

        # The pinned library selects its socket class in MPVInter.__init__.
        # Serialize construction, restore immediately, and retain normal teardown.
        setattr(library, name, VerifiedSocket)
        try:
            return library.MPV(start_mpv=False, ipc_socket=endpoint, **kwargs)
        finally:
            setattr(library, name, original)


def verify_unix_peer(connection, *, player_pid=None):
    import socket
    import struct

    if hasattr(socket, "SO_PEERCRED"):
        pid, uid, _ = struct.unpack(
            "3i",
            connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12),
        )
        if uid != os.getuid() or (player_pid is not None and pid != player_pid):
            raise SecurityError("Unexpected Unix IPC peer")
    elif hasattr(connection, "getpeereid"):
        uid, _ = connection.getpeereid()
        if uid != os.getuid():
            raise SecurityError("Unexpected Unix IPC peer")
    else:
        raise SecurityError("Native Unix peer credential API unavailable")
