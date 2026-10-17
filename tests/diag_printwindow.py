"""Is PrintWindow broken here, or is the capture code wrong?

Tests against a window this process creates itself, so "the app refuses to be captured"
cannot be the explanation. Reports PrintWindow's return value and the first pixels, and
tries a screen grab of the same rectangle as a control.
"""
import ctypes
import sys
import time
import tkinter as tk
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "printwindow_diag.log", "w", encoding="utf-8",
            errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from app import window_target as wt  # noqa: E402
from app.capture import ScreenCapture  # noqa: E402
from app.config import Region  # noqa: E402

user32 = wt.user32
gdi32 = wt.gdi32


def main() -> int:
    root = tk.Tk()
    root.title("PrintWindow 诊断用窗口")
    root.configure(bg="#202020")
    root.geometry("700x220+200+200")
    root.attributes("-topmost", True)
    tk.Label(root, text="HELLO PRINTWINDOW 12345", bg="#202020", fg="#ffffff",
             font=("Consolas", 30)).pack(expand=True)
    root.update()
    time.sleep(0.6)
    root.update()

    hwnd = int(root.frame(), 16) if isinstance(root.frame(), str) else int(root.frame())
    print(f"自建窗口 hwnd = {hwnd}")
    frame = wt.frame_rect(hwnd)
    print(f"frame_rect    = {frame}")
    print(f"client_rect   = {wt.client_rect(hwnd)}")

    rect = frame
    x, y, width, height = rect
    print(f"\n尺寸 {width}x{height}")

    screen_dc = user32.GetDC(0)
    mem_dc = gdi32.CreateCompatibleDC(screen_dc)
    info = wt.BITMAPINFO()
    info.bmiHeader.biSize = ctypes.sizeof(wt.BITMAPINFOHEADER)
    info.bmiHeader.biWidth = width
    info.bmiHeader.biHeight = -height
    info.bmiHeader.biPlanes = 1
    info.bmiHeader.biBitCount = 32
    info.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    bitmap = gdi32.CreateDIBSection(mem_dc, ctypes.byref(info), 0,
                                    ctypes.byref(bits), None, 0)
    print(f"mem_dc={mem_dc}  bitmap={bitmap}  bits={bits.value}")
    gdi32.SelectObject(mem_dc, bitmap)

    for flag, label in ((wt.PW_RENDERFULLCONTENT, "PW_RENDERFULLCONTENT(2)"),
                        (0, "flag=0")):
        ctypes.memset(bits, 0, width * height * 4)
        ctypes.set_last_error(0)
        ok = user32.PrintWindow(wintypes.HWND(hwnd), mem_dc, ctypes.c_uint(flag))
        err = ctypes.get_last_error()
        raw = np.frombuffer(ctypes.string_at(bits, width * height * 4), dtype=np.uint8)
        arr = raw.reshape(height, width, 4)
        nonzero = int((arr[:, :, :3] != 0).sum())
        print(f"\n{label}: PrintWindow 返回 {ok}  GetLastError={err}")
        print(f"  非零像素 {nonzero} / {width * height}"
              f"（{100.0 * nonzero / (width * height):.2f}%）")
        print(f"  平均亮度 {arr[:, :, :3].mean():.1f}")
        path = ROOT / "tests" / f"diag_printwindow_{flag}.png"
        Image.fromarray(arr[:, :, 2::-1]).save(path)
        print(f"  已存 {path.relative_to(ROOT)}")
        if nonzero:
            break

    print("\n对照组：直接截这块屏幕区域")
    try:
        shot = ScreenCapture().grab(Region(left=x, top=y, width=width, height=height))
        path = ROOT / "tests" / "diag_screen_grab.png"
        Image.fromarray(shot[:, :, ::-1]).save(path)
        print(f"  形状 {shot.shape}  平均亮度 {shot.mean():.1f}")
        print(f"  已存 {path.relative_to(ROOT)}")
    except Exception as exc:  # noqa: BLE001
        print(f"  失败：{type(exc).__name__}: {exc}")

    gdi32.DeleteObject(bitmap)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(0, screen_dc)
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
