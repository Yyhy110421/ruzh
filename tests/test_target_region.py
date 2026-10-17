"""A window-relative selection must never silently collapse to nothing.

The bug this pins down: the stored offset can point outside the window - the window was
resized after the selection, or the number is really a screen coordinate from the other
mode. resolve_region clamped the *size* to zero in that case, so the app reported
"目标窗口不可用" with the window plainly visible on screen, captured nothing, and never
translated a character.

Run: python tests/test_target_region.py
"""
import queue
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "target_region.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import window_target as wt  # noqa: E402
from app.config import AppConfig, Region  # noqa: E402

failures = 0


def check(ok, label, extra=""):
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


def fake_window(handle=1, client=(100, 200, 800, 600)):
    wt.find_window = lambda *a, **k: handle
    wt.client_rect = lambda h: client


def cfg_with(region, mode="window"):
    cfg = AppConfig()
    cfg.target_mode = mode
    cfg.target_window_title = "Some Window"
    cfg.target_window_process = "some.exe"
    cfg.region = Region(*region)
    return cfg


def main() -> int:
    print("=" * 78)
    print("一、正常的窗口内偏移")
    print("=" * 78)
    fake_window()
    got = wt.resolve_region(cfg_with((0, 0, 800, 600)))
    check(got == (100, 200, 800, 600), f"整窗: {got}")
    got = wt.resolve_region(cfg_with((100, 100, 300, 200)))
    check(got == (200, 300, 300, 200), f"窗口内 (100,100)+(300x200): {got}")
    print()

    print("=" * 78)
    print("二、偏移越界 —— 以前会压成 0 宽，现在跟整窗")
    print("=" * 78)
    # This is the exact shape of the report: 1328 > the 800-wide client area.
    got = wt.resolve_region(cfg_with((1328, 154, 538, 757)))
    check(got == (100, 200, 800, 600), f"越界 -> 整窗，不再是 0 宽: {got}")
    check(got[2] > 0 and got[3] > 0, f"宽高都不为 0: {got[2]}x{got[3]}")
    got = wt.resolve_region(cfg_with((0, 900, 538, 757)))
    check(got == (100, 200, 800, 600), f"纵向越界 -> 整窗: {got}")
    print()

    print("=" * 78)
    print("三、负偏移、贴边、超出右边")
    print("=" * 78)
    got = wt.resolve_region(cfg_with((-50, -50, 300, 200)))
    check(got == (100, 200, 300, 200), f"负偏移拉回 0: {got}")
    got = wt.resolve_region(cfg_with((700, 500, 300, 200)))
    check(got == (800, 700, 100, 100), f"超出右边被裁到 100x100: {got}")
    check(got[2] > 0, "裁完还有宽度")
    print()

    print("=" * 78)
    print("四、窗口没了才返回 0")
    print("=" * 78)
    fake_window(handle=0)
    got = wt.resolve_region(cfg_with((0, 0, 800, 600)))
    check(got == (0, 0, 0, 0), f"找不到窗口: {got}")
    fake_window(handle=1, client=None)
    got = wt.resolve_region(cfg_with((0, 0, 800, 600)))
    check(got == (0, 0, 0, 0), f"拿不到客户区: {got}")
    print()

    print("=" * 78)
    print("五、固定区域模式不受影响")
    print("=" * 78)
    fake_window()
    cfg = cfg_with((1328, 154, 538, 757), mode="region")
    got = wt.resolve_region(cfg)
    check(got == (1328, 154, 538, 757), f"固定区域原样返回: {got}")
    print()

    print("=" * 78)
    print(f"失败 {failures} 项")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
