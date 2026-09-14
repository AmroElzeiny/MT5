"""Measure the resident/commit footprint of one benchmark child after importing ai_gate."""
import ctypes
import ctypes.wintypes
import os
import sys
import time
from pathlib import Path

REPO_PY = Path(__file__).resolve().parents[4] / "python"
sys.path.insert(0, str(REPO_PY))
sys.path.insert(0, str(REPO_PY / "tools"))
import opencode_reasoning_benchmark as bench  # noqa: E402

scratch = Path(__file__).resolve().parent / "_memprobe_scratch"
bench.configure_child_env(scratch, mode="dry", bus=Path(bench.DEFAULT_BUS))
t0 = time.time()
import ai_gate  # noqa: E402,F401


class PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.wintypes.DWORD), ("PageFaultCount", ctypes.wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]


pmc = PMC()
pmc.cb = ctypes.sizeof(PMC)
ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb)
print(f"import_sec={time.time()-t0:.1f} ws_mb={pmc.WorkingSetSize/2**20:.0f} "
      f"peak_ws_mb={pmc.PeakWorkingSetSize/2**20:.0f} private_mb={pmc.PagefileUsage/2**20:.0f}")
