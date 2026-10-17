"""Dev test: Russian pairs (direct + pivoted) and the NLLB engine.

Reports latency and peak working set per engine so the resource cost of NLLB is
measured rather than guessed.
"""
import ctypes
import os
import sys
import time
import traceback
from ctypes import wintypes

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_LOG = open(os.path.join(ROOT, "tests", "langs_out.log"), "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import engine as engine_mod  # noqa: E402
from app import models  # noqa: E402
from app.config import AppConfig  # noqa: E402


class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
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


def memory_mb() -> tuple[float, float]:
    """(current working set, peak working set) in MB."""
    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(counters)
    ctypes.windll.psapi.GetProcessMemoryInfo(
        ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb)
    return counters.WorkingSetSize / 1048576, counters.PeakWorkingSetSize / 1048576


SAMPLES = {
    ("ru", "zh"): "Встреча перенесена на три часа дня завтра.",
    ("zh", "ru"): "会议已移至明天下午三点。",
    ("ru", "en"): "Мы должны поговорить о том, что случилось вчера вечером.",
    ("en", "ru"): "The government announced new measures to reduce air pollution.",
    ("en", "zh"): "Please make sure the file is saved before you shut down the computer.",
    ("zh", "en"): "政府宣布了减少大城市空气污染的新措施。",
}


def run_engine(engine_key: str, pairs: list[tuple[str, str]]) -> int:
    print("=" * 72)
    print(f"引擎: {engine_key}   ({engine_mod.engine_label(engine_key)})")
    print("=" * 72)
    root = os.path.join(ROOT, "models")
    before_cur, _ = memory_mb()
    failures = 0

    for src, tgt in pairs:
        cfg = AppConfig()
        cfg.source_lang, cfg.target_lang = src, tgt
        cfg.translate.engine = engine_key

        ready, detail = engine_mod.check(cfg, root)
        if not ready:
            print(f"  跳过 {src}->{tgt}: {detail}")
            continue

        try:
            route = engine_mod.build(cfg, root)
            t0 = time.perf_counter()
            route.load()
            load_s = time.perf_counter() - t0
            cur, peak = memory_mb()
            print(f"\n  [{src}->{tgt}] {route.label}"
                  + ("  （经中转）" if route.is_pivot else ""))
            print(f"    加载 {load_s:.1f}s   内存 {cur:.0f} MB（峰值 {peak:.0f} MB）")

            text = SAMPLES.get((src, tgt), "")
            if not text:
                continue
            t0 = time.perf_counter()
            out = route.translate(text)
            dt = (time.perf_counter() - t0) * 1000
            print(f"    {dt:6.0f} ms  {text}")
            print(f"              -> {out}")
            if not out.strip():
                print("    [FAIL] 空输出")
                failures += 1
            del route
        except Exception:
            traceback.print_exc()
            failures += 1

    after_cur, after_peak = memory_mb()
    print(f"\n  引擎整体内存：开始时 {before_cur:.0f} MB → 结束后 {after_cur:.0f} MB"
          f"（峰值 {after_peak:.0f} MB）")
    return failures


def main() -> int:
    failures = 0

    print("模型状态：")
    root = os.path.join(ROOT, "models")
    for src, tgt in models.all_supported_pairs():
        state = "已下载" if models.is_installed(root, src, tgt) else "缺失"
        print(f"  OPUS {src}->{tgt:3s} {state}   {models.route_label(src, tgt)}")
    for key in models.MULTILINGUAL:
        state = "已下载" if models.multilingual_installed(root, key) else "缺失"
        print(f"  多语言 {key}: {state}")

    failures += run_engine("opus", [("ru", "zh"), ("zh", "ru"), ("ru", "en"),
                                   ("en", "ru"), ("en", "zh"), ("zh", "en")])

    key = "nllb-200-distilled-600M"
    if models.multilingual_installed(root, key):
        failures += run_engine("nllb", [("ru", "zh"), ("zh", "ru"), ("en", "zh")])
    else:
        print("\nNLLB 尚未下载完整，跳过。")

    print("\n" + "=" * 72)
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
