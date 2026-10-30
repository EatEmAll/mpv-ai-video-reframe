"""Per-process dedicated GPU memory: Windows PDH counters or Linux /proc."""

import ctypes
import json
import os
import time
from ctypes import wintypes
from pathlib import Path
from threading import Event, Thread


class CounterValue(ctypes.Structure):
    class Value(ctypes.Union):
        _fields_ = [("large", ctypes.c_longlong), ("double", ctypes.c_double)]

    _fields_ = [("status", wintypes.DWORD), ("value", Value)]


class CounterItem(ctypes.Structure):
    _fields_ = [("name", wintypes.LPWSTR), ("value", CounterValue)]


class GPUMemory:
    def __init__(self, pid=None):
        if os.name != "nt":
            raise OSError("Windows GPU counters required")
        self.pid = pid or os.getpid()
        self.dll = ctypes.WinDLL("pdh")
        self.query = wintypes.HANDLE()
        self.counter = wintypes.HANDLE()
        self.dll.PdhOpenQueryW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self.dll.PdhAddEnglishCounterW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            ctypes.c_size_t,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self.dll.PdhCollectQueryData.argtypes = [wintypes.HANDLE]
        self.dll.PdhGetFormattedCounterArrayW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
            ctypes.c_void_p,
        ]
        self.dll.PdhCloseQuery.argtypes = [wintypes.HANDLE]
        status = self.dll.PdhOpenQueryW(None, 0, ctypes.byref(self.query))
        if status:
            raise OSError(f"PdhOpenQuery failed: {status:#x}")
        slash = chr(92)
        path = slash + "GPU Process Memory(*)" + slash + "Dedicated Usage"
        status = self.dll.PdhAddEnglishCounterW(
            self.query, path, 0, ctypes.byref(self.counter)
        )
        if status:
            self.close()
            raise OSError(f"GPU memory counter unavailable: {status:#x}")

    def sample(self):
        status = self.dll.PdhCollectQueryData(self.query)
        if status:
            raise OSError(f"GPU memory collect failed: {status:#x}")
        size = wintypes.DWORD()
        count = wintypes.DWORD()
        status = (
            self.dll.PdhGetFormattedCounterArrayW(
                self.counter, 0x400, ctypes.byref(size), ctypes.byref(count), None
            )
            & 0xFFFFFFFF
        )
        if status != 0x800007D2:
            raise OSError(f"GPU memory sizing failed: {status:#x}")
        buffer = ctypes.create_string_buffer(size.value)
        status = self.dll.PdhGetFormattedCounterArrayW(
            self.counter, 0x400, ctypes.byref(size), ctypes.byref(count), buffer
        )
        if status:
            raise OSError(f"GPU memory read failed: {status:#x}")
        items = ctypes.cast(buffer, ctypes.POINTER(CounterItem))
        matches = [
            items[i].value.value.large
            for i in range(count.value)
            if items[i].name.startswith(f"pid_{self.pid}_")
            and items[i].value.status in (0, 1)
        ]
        # A valid counter array with no GPU allocation for this live process is
        # zero, not an unavailable measurement. Query failures still raise.
        return sum(matches)

    def close(self):
        if self.query:
            self.dll.PdhCloseQuery(self.query)
            self.query = wintypes.HANDLE()


def nvml_device():
    """Whole-device NVIDIA utilization, absent on machines without NVML."""
    import pynvml

    try:
        pynvml.nvmlInit()
    except pynvml.NVMLError:
        if os.name == "nt":
            raise
        return None
    return pynvml.nvmlDeviceGetHandleByIndex(0)


def nvml_sample(gpu):
    if gpu is None:
        return {"gpu_utilization": None, "gpu_memory_used_bytes": None}
    import pynvml

    return {
        "gpu_utilization": pynvml.nvmlDeviceGetUtilizationRates(gpu).gpu,
        "gpu_memory_used_bytes": pynvml.nvmlDeviceGetMemoryInfo(gpu).used,
    }


def nvml_close():
    import pynvml

    pynvml.nvmlShutdown()


class LinuxGPUMemory:
    """Attribute GPU memory from a process's open device handles.

    No GPU device handle is a measured zero. A handle whose memory cannot be
    read raises, leaving the gate unmeasured rather than passing it.
    """

    def __init__(self, pid=None, proc=Path("/proc"), nvidia_used=None):
        self.pid = pid or os.getpid()
        self.proc = Path(proc)
        self.nvidia_used = nvidia_used or self._nvidia_used

    def _nvidia_used(self):
        import pynvml

        pynvml.nvmlInit()
        try:
            total = 0
            for index in range(pynvml.nvmlDeviceGetCount()):
                gpu = pynvml.nvmlDeviceGetHandleByIndex(index)
                for query in (
                    pynvml.nvmlDeviceGetComputeRunningProcesses,
                    pynvml.nvmlDeviceGetGraphicsRunningProcesses,
                ):
                    for entry in query(gpu):
                        if entry.pid == self.pid and entry.usedGpuMemory:
                            total += entry.usedGpuMemory
            return total
        finally:
            pynvml.nvmlShutdown()

    def _drm_bytes(self, descriptor):
        clients, total = None, 0
        text = (self.proc / str(self.pid) / "fdinfo" / descriptor).read_text()
        for line in text.splitlines():
            key, _, value = line.partition(":")
            if key == "drm-client-id":
                clients = value.strip()
            elif key.startswith(("drm-total-", "drm-memory-")):
                amount, *unit = value.split()
                total += int(amount) * {"KiB": 1024, "MiB": 1024**2}.get(
                    unit[0] if unit else "", 1
                )
        if clients is None:
            raise OSError("DRM memory statistics unavailable")
        return clients, total

    def sample(self):
        directory = self.proc / str(self.pid) / "fd"
        nvidia, clients = False, {}
        for descriptor in os.listdir(directory):
            try:
                target = os.readlink(directory / descriptor)
            except FileNotFoundError:
                continue  # closed between listing and reading
            if target.startswith("/dev/nvidia"):
                nvidia = True
            elif target.startswith("/dev/dri/"):
                client, used = self._drm_bytes(descriptor)
                clients[client] = used
        return sum(clients.values()) + (self.nvidia_used() if nvidia else 0)

    def close(self):
        pass


class ResourceMonitor(Thread):
    def __init__(self, path, pid=None):
        self.pid = pid or os.getpid()
        super().__init__(name="reframe-resources", daemon=True)
        self.path = Path(path)
        self.stop_event = Event()
        self.peak_bytes = None
        self.error = None

    def run(self):
        memory = gpu = None
        try:
            import psutil

            memory = (GPUMemory if os.name == "nt" else LinuxGPUMemory)(self.pid)
            gpu = nvml_device()
            process = psutil.Process(self.pid)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("w", encoding="utf-8") as log:
                while not self.stop_event.is_set():
                    dedicated = memory.sample()
                    if dedicated is not None:
                        self.peak_bytes = max(self.peak_bytes or 0, dedicated)
                    record = {
                        "wall": time.time(),
                        "pid": self.pid,
                        "process_dedicated_bytes": dedicated,
                        "cpu_percent": process.cpu_percent(),
                        "rss_bytes": process.memory_info().rss,
                        **nvml_sample(gpu),
                    }
                    log.write(json.dumps(record) + "\n")
                    log.flush()
                    self.stop_event.wait(0.5)
        except Exception as error:
            self.error = repr(error)
        finally:
            if memory:
                memory.close()
            if gpu is not None:
                nvml_close()

    def close(self):
        self.stop_event.set()
        self.join(timeout=10)
        if self.is_alive():
            self.error = "Resource monitor did not stop"
