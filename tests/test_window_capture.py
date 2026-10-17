"""Window targeting: enumeration, and the region following a window that moves.

This replaces an earlier version that asserted PrintWindow must succeed. That was the
wrong contract: PrintWindow is denied outright for other processes' windows in some
environments (ERROR_ACCESS_DENIED), and for hardware-accelerated windows it returns a
black rectangle. Neither is a failure - the capture falls back to grabbing the window's
screen rectangle, which is what makes the feature work. So what is tested here is the
part the user depends on: the rectangle follows the window.
"""
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "window_target.log", "w", encoding="utf-8",
            errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

import numpy as np  # noqa: E402

from app import window_target as wt  # noqa: E402
from app.capture import ScreenCapture  # noqa: E402
from app.config import AppConfig, Region  # noqa: E402

TITLE = "窗口跟随-自检"
failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    if condition:
        print(f"  [OK  ] {label}")
    else:
        failures += 1
        print(f"  [FAIL] {label}")


def main() -> int:
    print("=" * 78)
    print("一、窗口枚举")
    print("=" * 78)
    windows = wt.list_windows()
    print(f"  找到 {len(windows)} 个可见窗口")
    for info in windows[:6]:
        print(f"    {info.client_width:>5}x{info.client_height:<5} {info.label}")
    check(len(windows) >= 1, "至少能列出一个窗口")
    check(all(w.client_width > 0 and w.client_height > 0 for w in windows),
          "每个窗口都有合理的内容区尺寸")

    print()
    print("=" * 78)
    print("二、PrintWindow 被拒绝时必须优雅回退，而不是报错")
    print("=" * 78)
    outcomes = []
    for info in windows[:6]:
        try:
            frame = wt.capture_frame(info.handle)
            outcomes.append("captured" if frame is not None else "refused")
        except Exception as exc:  # noqa: BLE001
            outcomes.append(f"raised:{type(exc).__name__}")
    print(f"  结果分布: {outcomes}")
    check(not any(o.startswith("raised") for o in outcomes),
          "被拒绝时返回 None，不抛异常")
    check(len(outcomes) == len(windows[:6]), "每个窗口都给出了结果")

    print()
    print("=" * 78)
    print("三、区域跟着窗口移动（这是用户真正依赖的部分）")
    print("=" * 78)
    root = tk.Tk()
    root.title(TITLE)
    root.configure(bg="#101014")
    root.geometry("640x280+260+320")
    root.attributes("-topmost", True)
    tk.Label(root, text="Цена указана за одну тонну", bg="#101014", fg="#eeeeee",
             font=("Segoe UI", 20)).pack(expand=True)
    root.update()
    time.sleep(0.5)
    root.update()

    cfg = AppConfig()
    cfg.target_mode = "window"
    cfg.target_window_title = TITLE
    cfg.target_window_process = "python.exe"
    cfg.region = Region(left=15, top=40, width=560, height=90)

    origins = []
    try:
        for x, y in ((260, 320), (820, 240), (340, 640)):
            root.geometry(f"+{x}+{y}")
            root.update()
            time.sleep(0.35)
            root.update()
            left, top, width, height = wt.resolve_region(cfg)
            origins.append((left, top))
            print(f"  窗口在 ({x:>4},{y:>4})  ->  截屏区域 ({left:>4},{top:>4}) "
                  f"{width}x{height}")
        check(len(set(origins)) == 3, "三次移动产生了三个不同的截屏原点")
        check(all(w == 560 and h == 90 for _, _, w, h in
                  [wt.resolve_region(cfg)]), "截屏尺寸保持不变")

        # The offset from the window position must be constant, which is the whole
        # mechanism: a fixed distance inside the window.
        offsets = {(left - x, top - y) for (left, top), (x, y)
                   in zip(origins, ((260, 320), (820, 240), (340, 640)))}
        check(len(offsets) == 1, f"相对窗口的偏移恒定（{offsets}）")

        print()
        print("  实际截一张，确认窗口内容在画面里：")
        left, top, width, height = wt.resolve_region(cfg)
        frame = ScreenCapture().grab(Region(left=left, top=top, width=width,
                                            height=height))
        check(isinstance(frame, np.ndarray) and frame.size > 0,
              f"能截到图像 {frame.shape if hasattr(frame, 'shape') else '?'}")
        check(float(frame.mean()) > 5, f"画面不是全黑（平均亮度 {float(frame.mean()):.0f}）")

        print()
        print("  对照组：固定区域模式不跟随")
        cfg.target_mode = "region"
        cfg.region = Region(left=15, top=40, width=560, height=90)
        fixed = {wt.resolve_region(cfg)[:2] for _ in range(3)}
        check(len(fixed) == 1, "固定模式下截屏原点始终不变（不跟随窗口）")
    finally:
        root.destroy()

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
