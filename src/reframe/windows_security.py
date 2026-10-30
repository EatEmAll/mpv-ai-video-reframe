"""Native Win32 DACL inspection. No shell commands or third-party ACL parser."""

import ctypes as c
import time
from ctypes import wintypes as w

from .security import SecurityError

adv = c.WinDLL("advapi32", use_last_error=True)
kernel = c.WinDLL("kernel32", use_last_error=True)
P = c.c_void_p
kernel.GetCurrentProcess.restype = w.HANDLE
kernel.LocalFree.argtypes = [P]
kernel.CloseHandle.argtypes = [w.HANDLE]
adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
adv.GetTokenInformation.argtypes = [w.HANDLE, c.c_int, P, w.DWORD, c.POINTER(w.DWORD)]
adv.ConvertSidToStringSidW.argtypes = [P, c.POINTER(w.LPWSTR)]
adv.GetNamedSecurityInfoW.argtypes = [
    w.LPWSTR,
    c.c_int,
    w.DWORD,
    c.POINTER(P),
    P,
    c.POINTER(P),
    P,
    c.POINTER(P),
]
adv.GetSecurityInfo.argtypes = [
    w.HANDLE,
    c.c_int,
    w.DWORD,
    c.POINTER(P),
    P,
    c.POINTER(P),
    P,
    c.POINTER(P),
]
adv.GetAce.argtypes = [P, w.DWORD, c.POINTER(P)]
adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
    w.LPCWSTR,
    w.DWORD,
    c.POINTER(P),
    P,
]
adv.GetSecurityDescriptorDacl.argtypes = [
    P,
    c.POINTER(w.BOOL),
    c.POINTER(P),
    c.POINTER(w.BOOL),
]
adv.SetSecurityInfo.argtypes = [w.HANDLE, c.c_int, w.DWORD, P, P, P, P]
kernel.CreateFileW.argtypes = [
    w.LPCWSTR,
    w.DWORD,
    w.DWORD,
    P,
    w.DWORD,
    w.DWORD,
    w.HANDLE,
]
kernel.CreateFileW.restype = w.HANDLE
kernel.GetNamedPipeServerProcessId.argtypes = [w.HANDLE, c.POINTER(w.ULONG)]
kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, P]


def checked(ok):
    if not ok:
        raise c.WinError(c.get_last_error())


def sid_text(sid):
    value = w.LPWSTR()
    checked(adv.ConvertSidToStringSidW(sid, c.byref(value)))
    try:
        return value.value
    finally:
        kernel.LocalFree(c.cast(value, P))


def user_sid():
    token = w.HANDLE()
    checked(adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token)))
    try:
        size = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, c.byref(size))
        buffer = c.create_string_buffer(size.value)
        checked(adv.GetTokenInformation(token, 1, buffer, size, c.byref(size)))
        return sid_text(c.cast(buffer, c.POINTER(P))[0])
    finally:
        kernel.CloseHandle(token)


def allowed_sids():
    return {
        user_sid(),
        "S-1-5-18",
        "S-1-5-32-544",
        "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
    }


def inspect(owner, dacl, *, private, ancestor=False):
    allowed = allowed_sids()
    if not owner or sid_text(owner) not in allowed or not dacl:
        raise SecurityError("Untrusted Windows owner or null DACL")
    # ACL header: revision, reserved, size, ACE count, reserved.
    count = c.cast(dacl, c.POINTER(c.c_ushort))[2]
    for index in range(count):
        ace = P()
        checked(adv.GetAce(dacl, index, c.byref(ace)))
        header = c.string_at(ace, 8)
        kind, flags = header[:2]
        if flags & 8:  # INHERIT_ONLY_ACE does not grant access to this object
            continue
        if kind == 1:  # deny ACE; ignoring it is conservative
            continue
        if kind != 0:
            raise SecurityError("Unsupported Windows ACL entry; refusing endpoint/path")
        mask = int.from_bytes(header[4:8], "little")
        sid = sid_text(ace.value + 8)
        unsafe = 0xD0152 if ancestor else 0xD0156
        unsafe |= 0x50000000  # GENERIC_ALL | GENERIC_WRITE
        if sid not in allowed and (private or mask & unsafe):
            raise SecurityError("Windows ACL grants another identity access")


def check_path(path, *, private=False, ancestor=False):
    owner, dacl, descriptor = P(), P(), P()
    error = adv.GetNamedSecurityInfoW(
        str(path), 1, 5, c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor)
    )
    if error:
        raise c.WinError(error)
    try:
        inspect(owner, dacl, private=private, ancestor=ancestor)
    finally:
        kernel.LocalFree(descriptor)


def private_descriptor(inherit=False):
    descriptor = P()
    flags = "OICI" if inherit else ""
    checked(
        adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            f"D:P(A;{flags};FA;;;{user_sid()})(A;{flags};FA;;;SY)(A;{flags};FA;;;BA)",
            1,
            c.byref(descriptor),
            None,
        )
    )
    return descriptor


def make_private_dir(path):
    class Attributes(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", P), ("inherit", w.BOOL)]

    descriptor = private_descriptor(inherit=True)
    try:
        attributes = Attributes(c.sizeof(Attributes), descriptor, False)
        checked(kernel.CreateDirectoryW(str(path), c.byref(attributes)))
    finally:
        kernel.LocalFree(descriptor)


def check_pipe(name, *, player_pid=None, harden=False):
    # SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION: do not let a pipe server
    # impersonate this client. Never request remote pipes or use TCP.
    deadline = time.monotonic() + 1
    while True:
        handle = kernel.CreateFileW(
            "\\\\.\\pipe\\" + name,
            0x20000 | (0x40000 if harden else 0),
            0,
            None,
            3,
            0x110000,
            None,
        )
        if handle != w.HANDLE(-1).value:
            break
        error = c.get_last_error()
        if error not in (2, 231) or time.monotonic() >= deadline:
            raise c.WinError(error)
        time.sleep(0.02)  # bounded startup/accept-loop delay, never retry ACL denial
    try:
        verify_pipe_handle(handle, player_pid=player_pid, harden=harden)
    finally:
        kernel.CloseHandle(handle)


def verify_pipe_handle(handle, *, player_pid=None, harden=False):
    pid = w.ULONG()
    checked(kernel.GetNamedPipeServerProcessId(handle, c.byref(pid)))
    if player_pid is not None and pid.value != player_pid:
        raise SecurityError("IPC server is not the expected player process")
    owner, dacl, descriptor = P(), P(), P()
    error = adv.GetSecurityInfo(
        handle, 6, 5, c.byref(owner), None, c.byref(dacl), None, c.byref(descriptor)
    )
    if error:
        raise c.WinError(error)
    try:
        if not owner or sid_text(owner) != user_sid():
            raise SecurityError("IPC server is not owned by the current user")
        if harden:
            restricted = private_descriptor()
            try:
                present, defaulted, new_dacl = w.BOOL(), w.BOOL(), P()
                checked(
                    adv.GetSecurityDescriptorDacl(
                        restricted,
                        c.byref(present),
                        c.byref(new_dacl),
                        c.byref(defaulted),
                    )
                )
                error = adv.SetSecurityInfo(handle, 6, 4, None, None, new_dacl, None)
                if error:
                    raise c.WinError(error)
            finally:
                kernel.LocalFree(restricted)
        else:
            inspect(owner, dacl, private=True)
    finally:
        kernel.LocalFree(descriptor)
    if harden:
        # Verify a freshly queried descriptor after mutation.
        error = adv.GetSecurityInfo(
            handle,
            6,
            5,
            c.byref(owner),
            None,
            c.byref(dacl),
            None,
            c.byref(descriptor),
        )
        if error:
            raise c.WinError(error)
        try:
            inspect(owner, dacl, private=True)
        finally:
            kernel.LocalFree(descriptor)
