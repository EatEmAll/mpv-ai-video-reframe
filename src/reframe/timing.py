"""Scoped timing hints; Unix uses its native high-resolution waits."""

import ctypes
import os
from contextlib import contextmanager


class PowerState(ctypes.Structure):
    _fields_ = [
        ("Version", ctypes.c_uint32),
        ("ControlMask", ctypes.c_uint32),
        ("StateMask", ctypes.c_uint32),
    ]


@contextmanager
def honor_timer_resolution():
    """Keep background Windows 11 processes eligible for their requested timer."""
    kernel, previous, process, changed = None, None, None, False
    if os.name == "nt":
        try:
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = ctypes.c_void_p
            for name in ("GetProcessInformation", "SetProcessInformation"):
                fn = getattr(kernel, name)
                fn.argtypes = [
                    ctypes.c_void_p,
                    ctypes.c_int,
                    ctypes.c_void_p,
                    ctypes.c_uint32,
                ]
                fn.restype = ctypes.c_int
            process = kernel.GetCurrentProcess()
            previous = PowerState(1, 0, 0)
            if kernel.GetProcessInformation(
                process, 4, ctypes.byref(previous), ctypes.sizeof(previous)
            ):
                requested = PowerState(
                    1, previous.ControlMask | 4, previous.StateMask & ~4
                )
                changed = bool(
                    kernel.SetProcessInformation(
                        process, 4, ctypes.byref(requested), ctypes.sizeof(requested)
                    )
                )
        except (AttributeError, OSError):
            pass  # Optional on older Windows; never required on Unix.
    try:
        yield
    finally:
        if changed:
            kernel.SetProcessInformation(
                process, 4, ctypes.byref(previous), ctypes.sizeof(previous)
            )


@contextmanager
def frame_timer():
    with honor_timer_resolution():
        timer = ctypes.WinDLL("winmm") if os.name == "nt" else None
        active = timer is not None and timer.timeBeginPeriod(1) == 0
        try:
            yield
        finally:
            if active:
                timer.timeEndPeriod(1)
