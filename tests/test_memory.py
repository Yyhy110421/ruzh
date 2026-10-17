"""Dev test: measure the resident memory each engine actually costs."""
import ctypes
import os
import sys
import time
import traceback
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_LOG = open(os.path.join(ROOT, "tests", "mem_out.log"), "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import engine as engine_mod  # noqa: E402
from app import models  # noqa: E402
from app.config import AppConfig  # noqa: E402
from app.translator import TranslateError  # noqa: E402


class PMC(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


_k32 = ctypes.WinDLL("kernel32", use_last_error=True)
_k32.GetCurrentProcess.restype = wintypes.HANDLE
# K32GetProcessMemoryInfo lives in kernel32 on Vista+; psapi.GetProcessMemoryInfo is
# the older alias and is what silently failed before. argtypes matter: without them
# ctypes treats the pseudo-handle as a signed int and refuses the large unsigned value.
_get_info = getattr(_k32, "K32GetProcessMemoryInfo", None)
if _get_info is None:
    _get_info = ctypes.WinDLL("psapi").GetProcessMemoryInfo
_get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
_get_info.restype = wintypes.BOOL


def memory_mb() -> tuple[float, float]:
    counters = PMC()
    counters.cb = ctypes.sizeof(counters)
    ok = _get_info(_k32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    if not ok:
        raise OSError(f"GetProcessMemoryInfo failed: {ctypes.get_last_error()}")
    return counters.WorkingSetSize / 1048576, counters.PeakWorkingSetSize / 1048576


def measure(name: str, cfg: AppConfig) -> None:
    root = os.path.join(ROOT, "models")
    before, _ = memory_mb()
    t0 = time.perf_counter()
    route = engine_mod.build(cfg, root)
    route.load()
    load_s = time.perf_counter() - t0
    after, peak = memory_mb()
    print(f"{name}")
    print(f"   加载 {load_s:5.1f}s   常驻 {before:6.0f} -> {after:6.0f} MB"
          f"   （净增 {after - before:6.0f} MB，峰值 {peak:.0f} MB）")
    del route


def main() -> int:
    base, _ = memory_mb()
    print(f"起始常驻内存: {base:.0f} MB\n")

    opus = AppConfig()
    opus.source_lang, opus.target_lang = "en", "zh"
    opus.translate.engine = "opus"
    measure("OPUS 英→中", opus)

    nllb = AppConfig()
    nllb.source_lang, nllb.target_lang = "ru", "zh"
    nllb.translate.engine = "nllb"
    try:
        measure("NLLB 俄→中", nllb)
    except TranslateError as exc:
        # NLLB is an optional ~1.3 GB download that doctor.py already reports as skipped
        # when it is absent. Measuring it must not turn the whole run into a failure.
        print(f"NLLB 俄→中：跳过（{exc}）")

    final, peak = memory_mb()
    print(f"\n进程最终常驻 {final:.0f} MB（峰值 {peak:.0f} MB）")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
