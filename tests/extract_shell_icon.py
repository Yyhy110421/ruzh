"""Render the icon Windows actually draws for a shortcut, and save it as a PNG.

SHGetFileInfo hands back a real HICON; drawing it into a DIB section and reading the
pixels back shows exactly what Explorer puts on the desktop, which settles whether a
wrong-looking shortcut is a broken icon or just a stale icon cache.
"""
import ctypes
import os
import sys
from ctypes import wintypes

SHGFI_ICON = 0x000000100
SHGFI_LARGEICON = 0x000000000
SHGFI_SMALLICON = 0x000000001
DI_NORMAL = 0x0003
DIB_RGB_COLORS = 0


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD), ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long), ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                   ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
shell32.SHGetFileInfoW.restype = ctypes.c_void_p


def grab(path: str, size: int, flags: int):
    from PIL import Image

    info = SHFILEINFOW()
    shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), flags)
    if not info.hIcon:
        return None, info.iIcon

    screen = user32.GetDC(0)
    memdc = gdi32.CreateCompatibleDC(screen)
    bmi = BITMAPINFO()
    bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.bmiHeader.biWidth = size
    bmi.bmiHeader.biHeight = -size          # negative: top-down rows
    bmi.bmiHeader.biPlanes = 1
    bmi.bmiHeader.biBitCount = 32
    bmi.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    hbmp = gdi32.CreateDIBSection(memdc, ctypes.byref(bmi), DIB_RGB_COLORS,
                                  ctypes.byref(bits), None, 0)
    old = gdi32.SelectObject(memdc, hbmp)
    user32.DrawIconEx(memdc, 0, 0, info.hIcon, size, size, 0, None, DI_NORMAL)
    gdi32.SelectObject(memdc, old)

    raw = ctypes.string_at(bits, size * size * 4)
    img = Image.frombuffer("RGBA", (size, size), raw, "raw", "BGRA", 0, 1).copy()
    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(memdc)
    user32.ReleaseDC(0, screen)
    user32.DestroyIcon(info.hIcon)
    return img, info.iIcon


def main() -> int:
    from PIL import Image

    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    lnk = os.path.join(desktop, "守译.lnk")
    ico = os.path.join(root, "assets", "app.ico")

    targets = [
        ("lnk_48", lnk, 48, SHGFI_ICON | SHGFI_LARGEICON),
        ("lnk_16", lnk, 16, SHGFI_ICON | SHGFI_SMALLICON),
        ("ico_48", ico, 48, SHGFI_ICON | SHGFI_LARGEICON),
    ]

    sheet = None
    for name, path, size, flags in targets:
        img, index = grab(path, size, flags)
        out = os.path.join(here, f"shellicon_{name}.png")
        if img is None:
            print(f"{name}: 没有取到图标 (hIcon null)")
            continue
        img.save(out)
        # A blank/generic icon shows up as a nearly uniform image.
        colours = len({px[:3] for px in img.getdata()})
        print(f"{name}: index={index}  不同颜色数={colours}  -> {out}")
        if sheet is None:
            sheet = img.copy()
        else:
            combined = Image.new("RGBA", (sheet.width + img.width + 12, max(sheet.height, img.height)),
                                 (255, 255, 255, 255))
            combined.paste(sheet, (0, 0), sheet)
            combined.paste(img, (sheet.width + 12, 0), img)
            sheet = combined

    if sheet is not None:
        path = os.path.join(here, "shellicon_compare.png")
        sheet.resize((sheet.width * 3, sheet.height * 3), 0).save(path)
        print("对比图 ->", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
