"""A transparent overlay drawn by a native Windows layered window.

Tk's Toplevel cannot do this: every attempt to combine -transparentcolor with the
click-through styles made the whole window vanish, measured six different ways. A colour
key then worked but GDI's glyphs came out with striped corruption, so this draws into a
32-bit DIB and hands it to UpdateLayeredWindow - per-pixel alpha, no colour key at all.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

WS_POPUP = 0x80000000
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOPMOST = 0x00000008
WS_EX_NOACTIVATE = 0x08000000
ULW_ALPHA = 0x00000002
AC_SRC_OVER = 0x00
AC_SRC_ALPHA = 0x01
DT_LEFT = 0x0000
DT_TOP = 0x0000
DT_WORDBREAK = 0x0010
DT_NOPREFIX = 0x0800
TRANSPARENT_BK = 1
BI_RGB = 0
DIB_RGB_COLORS = 0
#: Marker colour painted where there is no text; made alpha 0 afterwards.
BG = (0x0A, 0x0B, 0x0C)
FONT_FACE = "Microsoft YaHei"


class WNDCLASSW(ctypes.Structure):
    _fields_ = [("style", ctypes.c_uint), ("lpfnWndProc", ctypes.c_void_p),
                ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                ("hCursor", w.HANDLE), ("hbrBackground", w.HBRUSH),
                ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", ctypes.c_uint), ("biWidth", ctypes.c_int),
                ("biHeight", ctypes.c_int), ("biPlanes", ctypes.c_ushort),
                ("biBitCount", ctypes.c_ushort), ("biCompression", ctypes.c_uint),
                ("biSizeImage", ctypes.c_uint), ("biXPelsPerMeter", ctypes.c_int),
                ("biYPelsPerMeter", ctypes.c_int), ("biClrUsed", ctypes.c_uint),
                ("biClrImportant", ctypes.c_uint)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint * 3)]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_void_p, w.HWND, ctypes.c_uint, w.WPARAM, w.LPARAM)

user32.DefWindowProcW.restype = ctypes.c_void_p
user32.DefWindowProcW.argtypes = [w.HWND, ctypes.c_uint, w.WPARAM, w.LPARAM]
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.LoadCursorW.argtypes = [w.HINSTANCE, ctypes.c_void_p]
user32.CreateWindowExW.restype = w.HWND
user32.CreateWindowExW.argtypes = [ctypes.c_uint, w.LPCWSTR, w.LPCWSTR, ctypes.c_uint,
                                   ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                   w.HWND, w.HMENU, w.HINSTANCE, ctypes.c_void_p]
user32.GetDC.restype = w.HDC
user32.GetDC.argtypes = [w.HWND]
user32.ReleaseDC.argtypes = [w.HWND, w.HDC]
user32.ShowWindow.argtypes = [w.HWND, ctypes.c_int]
user32.DestroyWindow.argtypes = [w.HWND]
user32.UpdateLayeredWindow.argtypes = [
    w.HWND, w.HDC, ctypes.POINTER(w.POINT), ctypes.POINTER(w.SIZE), w.HDC,
    ctypes.POINTER(w.POINT), w.COLORREF, ctypes.POINTER(BLENDFUNCTION), ctypes.c_uint]
user32.DrawTextW.argtypes = [w.HDC, w.LPCWSTR, ctypes.c_int,
                             ctypes.POINTER(w.RECT), ctypes.c_uint]
gdi32.CreateCompatibleDC.restype = w.HDC
gdi32.CreateCompatibleDC.argtypes = [w.HDC]
gdi32.CreateDIBSection.restype = w.HANDLE
gdi32.CreateDIBSection.argtypes = [w.HDC, ctypes.POINTER(BITMAPINFO), ctypes.c_uint,
                                   ctypes.POINTER(ctypes.c_void_p), w.HANDLE, ctypes.c_uint]
gdi32.SelectObject.restype = w.HANDLE
gdi32.SelectObject.argtypes = [w.HDC, w.HANDLE]
gdi32.DeleteObject.argtypes = [w.HANDLE]
gdi32.DeleteDC.argtypes = [w.HDC]
gdi32.SetBkMode.argtypes = [w.HDC, ctypes.c_int]
gdi32.SetTextColor.argtypes = [w.HDC, w.COLORREF]
gdi32.CreateFontW.restype = w.HANDLE
gdi32.CreateFontW.argtypes = [ctypes.c_int] * 13 + [w.LPCWSTR]


class NativeOverlay:
    """Borderless, click-through, per-pixel-alpha window that renders translated blocks."""

    def __init__(self) -> None:
        self.items: list[tuple[float, float, float, float, str]] = []
        self._proc = WNDPROC(self._wndproc)
        hinst = kernel32.GetModuleHandleW(None)
        cls = WNDCLASSW()
        cls.lpfnWndProc = ctypes.cast(self._proc, ctypes.c_void_p)
        cls.hInstance = hinst
        cls.hCursor = user32.LoadCursorW(0, ctypes.c_void_p(32512))
        cls.lpszClassName = "RuZhOverlay2"
        cls.hbrBackground = 0
        user32.RegisterClassW(ctypes.byref(cls))
        ex = (WS_EX_LAYERED | WS_EX_TOOLWINDOW | WS_EX_TRANSPARENT
              | WS_EX_TOPMOST | WS_EX_NOACTIVATE)
        self.hwnd = user32.CreateWindowExW(
            ex, "RuZhOverlay2", "ruzh-overlay", WS_POPUP, 0, 0, 64, 64, 0, 0, hinst, 0)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def show(self, items, region=None) -> None:
        self.items = [(float(i[0]), float(i[1]), float(i[2]), float(i[3]), str(i[4]).strip())
                      for i in items if str(i[4]).strip()]
        if not self.hwnd or not self.items:
            return
        # The boxes are relative to the captured region, the window is on the screen.
        off_x = off_y = 0
        if region is not None and getattr(region, "valid", False):
            off_x, off_y = int(region.left), int(region.top)
        left = int(min(i[0] for i in self.items)) + off_x
        top = int(min(i[1] for i in self.items)) + off_y
        right = int(max(i[0] + i[2] for i in self.items)) + off_x + 4
        bottom = int(max(i[1] + i[3] for i in self.items)) + off_y + 4
        width, height = max(32, right - left), max(24, bottom - top)

        screen = user32.GetDC(0)
        mem = gdi32.CreateCompatibleDC(screen)
        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height          # top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi32.CreateDIBSection(mem, ctypes.byref(info), DIB_RGB_COLORS,
                                        ctypes.byref(bits), 0, 0)
        if not bitmap or not bits.value:
            gdi32.DeleteDC(mem)
            user32.ReleaseDC(0, screen)
            return
        old = gdi32.SelectObject(mem, bitmap)

        count = width * height
        buf = (ctypes.c_ubyte * (count * 4)).from_address(bits.value)
        # Paint the marker colour everywhere, alpha 0.
        for i in range(0, count * 4, 4):
            buf[i] = BG[2]
            buf[i + 1] = BG[1]
            buf[i + 2] = BG[0]
            buf[i + 3] = 0

        gdi32.SetBkMode(mem, TRANSPARENT_BK)
        gdi32.SetTextColor(mem, 0x00000000)      # black glyphs on the marker background
        for bl, bt, bw, bh, text in self.items:
            size = _fit_size(text, bw, bh)
            font = gdi32.CreateFontW(-size, 0, 0, 0, 400, 0, 0, 0, 134, 0, 0, 3, 0, FONT_FACE)
            prev = gdi32.SelectObject(mem, font)
            r = w.RECT(int(bl) + off_x - left, int(bt) + off_y - top,
                       int(bl) + off_x - left + int(bw), int(bt) + off_y - top + int(bh))
            user32.DrawTextW(mem, text, -1, ctypes.byref(r),
                             DT_LEFT | DT_TOP | DT_WORDBREAK | DT_NOPREFIX)
            gdi32.SelectObject(mem, prev)
            gdi32.DeleteObject(font)

        # Marker pixels become transparent, everything else opaque. GDI writes no alpha
        # channel, so it has to be derived from the colour.
        for i in range(0, count * 4, 4):
            if buf[i] == BG[2] and buf[i + 1] == BG[1] and buf[i + 2] == BG[0]:
                buf[i + 3] = 0
            else:
                buf[i + 3] = 255

        blend = BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        pos = w.POINT(left, top)
        size_l = w.SIZE(width, height)
        src = w.POINT(0, 0)
        user32.UpdateLayeredWindow(self.hwnd, screen, ctypes.byref(pos),
                                   ctypes.byref(size_l), mem, ctypes.byref(src), 0,
                                   ctypes.byref(blend), ULW_ALPHA)
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(0, screen)
        user32.ShowWindow(self.hwnd, 5)

    def hide(self) -> None:
        if self.hwnd:
            user32.ShowWindow(self.hwnd, 0)

    def destroy(self) -> None:
        if self.hwnd:
            user32.DestroyWindow(self.hwnd)
            self.hwnd = 0


def _fit_size(text: str, width: float, height: float) -> int:
    """Largest font size whose wrapped text still fits the box, floored at 9px."""
    size = int(max(11, min(30, height * 0.85)))
    while size > 9:
        per_line = max(1, int(width / (size * 0.62)))
        lines = max(1, -(-len(text) // per_line))
        if lines * size * 1.25 <= height + 2:
            return size
        size -= 1
    return 9
