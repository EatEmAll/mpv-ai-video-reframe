"""Native Linux security evidence. Run with Python, from a private Linux copy.

No media/GUI coverage. sudo is used only for a child dropping to nobody; run
`sudo -v` first. Without working sudo the other checks still run, and the script
reports the uid denial as not run and exits 3. That is partial evidence.
"""

import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

from reframe.security import SecurityError, prepare_endpoint, validate_endpoint
from reframe.transport import verify_unix_peer

assert sys.platform == "linux" and os.getuid() != 0
privileged = subprocess.run(["sudo", "-n", "true"], capture_output=True).returncode == 0
with tempfile.TemporaryDirectory(prefix="reframe-native-") as temporary:
    root = Path(temporary)
    os.environ["TMPDIR"] = temporary
    tempfile.tempdir = temporary
    old = os.umask(0)
    try:
        endpoint = Path(prepare_endpoint(os.getpid()))
        assert endpoint.parent.stat().st_mode & 0o777 == 0o700
        assert endpoint.parent.parent.stat().st_mode & 0o777 == 0o700
        server = socket.socket(socket.AF_UNIX)
        server.bind(str(endpoint))
        server.listen()
        validate_endpoint(str(endpoint), harden=True)
        assert endpoint.stat().st_mode & 0o777 == 0o600
        client = socket.socket(socket.AF_UNIX)
        client.connect(str(endpoint))
        accepted, _ = server.accept()
        verify_unix_peer(client, player_pid=os.getpid())
        try:
            verify_unix_peer(client, player_pid=os.getpid() + 1)
        except SecurityError:
            pass
        else:
            raise AssertionError("wrong PID accepted")
        if privileged:
            # A separate native uid, with all supplemental groups removed. This is
            # a disposable child, not a login/account/session modification.
            code = """import os,socket,sys
os.setgroups([]); os.setgid(65534); os.setuid(65534)
try: socket.socket(socket.AF_UNIX).connect(sys.argv[1])
except PermissionError: print('UID65534_DENIED'); sys.exit(0)
sys.exit(1)
"""
            result = subprocess.run(
                ["sudo", "-n", sys.executable, "-c", code, str(endpoint)],
                capture_output=True,
                text=True,
                check=True,
            )
            assert "UID65534_DENIED" in result.stdout
            # A root-owned peer deliberately binds a user-owned test socket. The
            # pathname check passes; connected SO_PEERCRED must still reject uid 0.
            peer_path = endpoint.parent / "wrong-peer"
            peer_code = """import os,socket,sys
s=socket.socket(socket.AF_UNIX); s.bind(sys.argv[1]); os.chown(sys.argv[1],int(sys.argv[2]),int(sys.argv[3])); os.chmod(sys.argv[1],0o600); s.listen()
print('READY',flush=True)
c,_=s.accept(); c.close(); s.close()
"""
            peer = subprocess.Popen(
                [
                    "sudo",
                    "-n",
                    sys.executable,
                    "-c",
                    peer_code,
                    str(peer_path),
                    str(os.getuid()),
                    str(os.getgid()),
                ],
                stdout=subprocess.PIPE,
                text=True,
            )
            try:
                assert peer.stdout.readline().strip() == "READY"
                validate_endpoint(str(peer_path))
                with socket.socket(socket.AF_UNIX) as connection:
                    connection.connect(str(peer_path))
                    try:
                        verify_unix_peer(connection)
                    except SecurityError:
                        pass
                    else:
                        raise AssertionError("wrong UID peer accepted")
                assert peer.wait(timeout=5) == 0
            finally:
                if peer.poll() is None:
                    peer.terminate()
                    peer.wait(timeout=5)
                peer.stdout.close()
                peer_path.unlink(missing_ok=True)
        accepted.close()
        client.close()
        server.close()
        # Closing a server leaves its pathname; application teardown does not
        # unlink by path. A second live endpoint must survive our cleanup.
        assert endpoint.exists()
        other = endpoint.parent / "other"
        with socket.socket(socket.AF_UNIX) as second:
            second.bind(str(other))
            endpoint.unlink()  # test-owned, closed endpoint only
            assert other.exists()
        other.unlink()
        endpoint.parent.rmdir()
        unsafe = root / "unsafe"
        unsafe.mkdir(mode=0o755)
        with socket.socket(socket.AF_UNIX) as second:
            second.bind(str(unsafe / "ipc"))
            try:
                validate_endpoint(str(unsafe / "ipc"))
            except SecurityError:
                pass
            else:
                raise AssertionError("unsafe parent accepted")
        # Any world-connectable system socket provides a real foreign-UID peer.
        bus = Path("/run/dbus/system_bus_socket")
        if bus.is_socket() and bus.stat().st_uid != os.getuid():
            with socket.socket(socket.AF_UNIX) as connection:
                connection.connect(str(bus))
                try:
                    verify_unix_peer(connection)
                except SecurityError:
                    pass
                else:
                    raise AssertionError("foreign UID peer accepted")
        if not privileged:
            print(
                "NATIVE_LINUX_PARTIAL: umask=000, 0700 parents, 0600 socket, peer PID, foreign peer refusal, owned cleanup; uid65534 denial NOT RUN (sudo unavailable)"
            )
            sys.exit(3)
        print(
            "NATIVE_LINUX_PASS: umask=000, 0700 parents, 0600 socket, peer UID/PID, wrong UID/PID refusal, uid65534 denial, owned cleanup"
        )
    finally:
        os.umask(old)
