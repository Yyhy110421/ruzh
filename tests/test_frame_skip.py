"""A still screen must not be re-recognised frame after frame.

Recognition measured ~7 s for a 900x700 window with 42 lines on this machine. Repeating
that while the user is reading - with only a blinking cursor changing - is most of why the
program felt slow. The pipeline now compares each frame against the previous one and
reuses the last result when nothing meaningful moved.

Run: python tests/test_frame_skip.py
"""
import queue
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "frame_skip.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

import numpy as np  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402

failures = 0


def check(ok, label, extra=""):
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


def fresh():
    return Pipeline(AppConfig(), queue.Queue())


def frame(seed=0, h=280, w=360):
    rng = np.random.default_rng(seed)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def main() -> int:
    print("=" * 78)
    print("一、第一帧必须识别")
    print("=" * 78)
    pl = fresh()
    check(pl._screen_changed(frame()) is True, "没见过这个画面 -> 要识别")
    print()

    print("=" * 78)
    print("二、一模一样的画面不再识别")
    print("=" * 78)
    same = frame()
    check(pl._screen_changed(same) is False, "像素完全一样 -> 跳过")
    check(pl._screen_changed(same) is False, "再来一次也跳过")
    print()

    print("=" * 78)
    print("三、光标闪烁这个级别的小变化仍然跳过")
    print("=" * 78)
    pl2 = fresh()
    base = np.zeros((280, 360, 3), dtype=np.uint8)
    base[:] = 200
    pl2._screen_changed(base)
    cursor = base.copy()
    cursor[100:112, 40:42] = 0          # 一个 12x2 的光标
    check(pl2._screen_changed(cursor) is False,
          "一个光标大小的变化不算变化", f"{cursor.size // 3} 像素里的 24 个")
    print()

    print("=" * 78)
    print("四、真的变了必须识别")
    print("=" * 78)
    pl3 = fresh()
    pl3._screen_changed(base)
    rolled = base.copy()
    rolled[:, :] = 40                   # 整屏变暗，例如换了页面
    check(pl3._screen_changed(rolled) is True, "整屏变化 -> 要识别")
    pl4 = fresh()
    pl4._screen_changed(base)
    panel = base.copy()
    panel[100:240, 60:300] = 10         # 中间弹出一大块
    check(pl4._screen_changed(panel) is True, "大块内容变化 -> 要识别")
    print()

    print("=" * 78)
    print("五、分辨率变了要识别（换了窗口大小）")
    print("=" * 78)
    pl5 = fresh()
    pl5._screen_changed(frame(h=280, w=360))
    check(pl5._screen_changed(frame(h=200, w=300)) is True, "尺寸变了 -> 要识别")
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
