"""Translate a chosen *window*, not a fixed patch of screen.

The first design asked the user to drag a box over a fixed screen area, which has a
fatal habit: move the window and the box is pointing at nothing. Everything here exists
to make the selection follow a window instead.

Two capture paths, because they fail in opposite situations:

* ``PrintWindow`` asks the window to draw itself into a bitmap. It keeps working when
  the window is partly covered or behind another one - which is the normal state when
  you are reading a chat window while doing something else.
* Grabbing the screen rectangle is the fallback, and the only option for content drawn
  with hardware acceleration, where ``PrintWindow`` returns black.

Coordinates are kept in two frames of reference and never mixed: *client* coordinates
are relative to the window's own content area, which is what the user's selection is
stored in; *screen* coordinates are absolute and only used when presenting or falling
back.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

import numpy as np

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
dwmapi = ctypes.WinDLL("dwmapi", use_last_error=True)

#: DWMWA_EXTENDED_FRAME_BOUNDS - the visible frame, excluding the invisible resize
#: border Windows 10 adds around windows. GetWindowRect includes that border, which
#: makes a captured window a few pixels too large on every side.
DWMWA_EXTENDED_FRAME_BOUNDS = 9

#: PrintWindow flag: render the full content rather than the frame only. Without it
#: many modern apps return an empty or title-bar-only bitmap.
PW_RENDERFULLCONTENT = 2

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0

user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                                  wintypes.LPARAM),
                               wintypes.LPARAM]
user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsIconic.argtypes = [wintypes.HWND]
user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                            ctypes.POINTER(wintypes.DWORD)]

# Handles are pointer-sized. Without explicit signatures ctypes assumes a C int, so a
# 64-bit HWND/HDC is truncated to 32 bits on the way in or out - which is why every
# window captured as pure black until these were declared. PrintWindow reported failure
# rather than corrupting anything, but the DC and bitmap handles were silently wrong.
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL
user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int


@dataclass
class WindowInfo:
    handle: int
    title: str
    process_id: int
    process_name: str
    #: Screen coordinates of the client area's top-left corner.
    client_x: int
    client_y: int
    client_width: int
    client_height: int

    @property
    def label(self) -> str:
        name = self.process_name or f"pid {self.process_id}"
        title = self.title.strip() or "(无标题)"
        if len(title) > 48:
            title = title[:46] + "…"
        return f"{title}  —  {name}"

    @property
    def usable(self) -> bool:
        return self.client_width >= 40 and self.client_height >= 40


def _title(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def _process_name(pid: int) -> str:
    """Best-effort executable name; needs no extra privileges for own-session apps."""
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buffer = ctypes.create_unicode_buffer(size.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buffer,
                                               ctypes.byref(size)):
            return buffer.value.rsplit("\\", 1)[-1]
    except Exception:  # noqa: BLE001
        pass
    finally:
        kernel32.CloseHandle(handle)
    return ""


def client_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(x, y, width, height) of the window's content area, in screen coordinates."""
    if not user32.IsWindow(hwnd) or user32.IsIconic(hwnd):
        return None
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    point = wintypes.POINT(0, 0)
    if not user32.ClientToScreen(hwnd, ctypes.byref(point)):
        return None
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return point.x, point.y, width, height


def frame_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """(x, y, width, height) of the whole visible frame, in screen coordinates."""
    if not user32.IsWindow(hwnd):
        return None
    rect = wintypes.RECT()
    # The DWM bounds exclude the invisible resize border; fall back to GetWindowRect
    # when DWM refuses (older systems, some layered windows).
    result = dwmapi.DwmGetWindowAttribute(
        wintypes.HWND(hwnd), ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
        ctypes.byref(rect), ctypes.sizeof(rect))
    if result != 0 or rect.right <= rect.left or rect.bottom <= rect.top:
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def is_alive(hwnd: int) -> bool:
    return bool(hwnd) and bool(user32.IsWindow(hwnd))


def is_minimized(hwnd: int) -> bool:
    return is_alive(hwnd) and bool(user32.IsIconic(hwnd))


def list_windows(include_own: bool = True, min_size: int = 120) -> list[WindowInfo]:
    """Every visible, non-minimised, titled top-level window."""
    found: list[WindowInfo] = []
    own_pid = ctypes.windll.kernel32.GetCurrentProcessId()

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def callback(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd) or user32.IsIconic(hwnd):
            return True
        title = _title(hwnd)
        rect = client_rect(hwnd)
        if not title or rect is None:
            return True
        x, y, width, height = rect
        if width < min_size or height < min_size:
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not include_own and pid.value == own_pid:
            return True
        info = WindowInfo(handle=int(hwnd), title=title, process_id=pid.value,
                          process_name=_process_name(pid.value),
                          client_x=x, client_y=y, client_width=width,
                          client_height=height)
        if info.usable:
            found.append(info)
        return True

    user32.EnumWindows(callback, 0)
    found.sort(key=lambda w: (w.process_name.lower(), w.title.lower()))
    return found


def find_window(title_contains: str, process_name: str = "") -> int:
    """Re-locate a window after a restart, by title and optionally process."""
    want_title = (title_contains or "").strip().lower()
    want_process = (process_name or "").strip().lower()
    if not want_title and not want_process:
        return 0
    windows = list_windows()

    def match(use_process: bool) -> int:
        for info in windows:
            if use_process and want_process and info.process_name.lower() != want_process:
                continue
            if want_title and want_title not in info.title.lower():
                continue
            return info.handle
        return 0

    found = match(use_process=True)
    if found:
        return found
    if want_title and want_process:
        # The stored process name can go stale - an app updates and the .exe is renamed, or
        # the same program is launched by a different host (python.exe vs pythonw.exe). The
        # title is what the user actually picked, so fall back to it rather than report the
        # window as gone while it is plainly on screen.
        return match(use_process=False)
    return 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class RGBQUAD(ctypes.Structure):
    _fields_ = [("rgbBlue", ctypes.c_ubyte), ("rgbGreen", ctypes.c_ubyte),
                ("rgbRed", ctypes.c_ubyte), ("rgbReserved", ctypes.c_ubyte)]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", RGBQUAD * 1)]


#: Declared after the structures they mention; see the note above on handle widths.
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateDIBSection.argtypes = [wintypes.HDC, ctypes.POINTER(BITMAPINFO),
                                   wintypes.UINT, ctypes.POINTER(ctypes.c_void_p),
                                   wintypes.HANDLE, wintypes.DWORD]
gdi32.CreateDIBSection.restype = wintypes.HBITMAP
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL


class _Bitmap:
    """A DIB section, wrapped so the GDI objects are always released.

    A DIB section rather than ``CreateCompatibleBitmap`` + ``GetDIBits``: GetDIBits
    returns nothing while the bitmap is still selected into its device context, and
    deselecting it first is easy to get wrong. A DIB section hands back a pointer to
    the pixels directly, which removes that whole class of mistake - every window
    captured as pure black was how it showed up the first time.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self.dc = gdi32.CreateCompatibleDC(0)
        if not self.dc:
            raise RuntimeError("CreateCompatibleDC 失败")

        info = BITMAPINFO()
        info.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.bmiHeader.biWidth = width
        info.bmiHeader.biHeight = -height        # negative: rows top-down
        info.bmiHeader.biPlanes = 1
        info.bmiHeader.biBitCount = 32
        info.bmiHeader.biCompression = 0         # BI_RGB

        self.bits = ctypes.c_void_p()
        self.handle = gdi32.CreateDIBSection(self.dc, ctypes.byref(info),
                                             DIB_RGB_COLORS,
                                             ctypes.byref(self.bits), None, 0)
        if not self.handle or not self.bits:
            gdi32.DeleteDC(self.dc)
            raise RuntimeError("CreateDIBSection 失败")
        self.previous = gdi32.SelectObject(self.dc, self.handle)

    def pixels_bgra(self) -> np.ndarray:
        size = self.width * self.height * 4
        raw = ctypes.string_at(self.bits, size)
        array = np.frombuffer(raw, dtype=np.uint8)
        return array.reshape(self.height, self.width, 4)

    def release(self) -> None:
        try:
            gdi32.SelectObject(self.dc, self.previous)
            gdi32.DeleteObject(self.handle)
            gdi32.DeleteDC(self.dc)
        except Exception:  # noqa: BLE001
            pass


def capture_frame(hwnd: int) -> np.ndarray | None:
    """Whole window (frame included) rendered by the window itself, as BGR.

    Returns None when the system refuses: PrintWindow is denied for other processes'
    windows in some environments (a sandboxed or restricted caller gets
    ERROR_ACCESS_DENIED), and hardware-accelerated windows render as a black rectangle.
    Neither is an error - the caller falls back to grabbing the window's screen
    rectangle, which is correct whenever the window is actually visible.
    """
    rect = frame_rect(hwnd)
    if rect is None:
        return None
    x, y, width, height = rect
    if width <= 0 or height <= 0 or width > 20000 or height > 20000:
        return None
    bitmap = _Bitmap(width, height)
    try:
        if not user32.PrintWindow(wintypes.HWND(hwnd), bitmap.dc,
                                  ctypes.c_uint(PW_RENDERFULLCONTENT)):
            return None
        bgra = bitmap.pixels_bgra()
    finally:
        bitmap.release()
    if not bgra.any():
        return None          # all-black: hardware accelerated, caller must fall back
    return np.ascontiguousarray(bgra[:, :, :3])


def resolve_region(cfg) -> tuple[int, int, int, int]:
    """The screen rectangle to capture, honouring the selected targeting mode.

    In window mode the stored region is an *offset inside the window's content area*,
    so this is what makes the selection travel with the window. Returns
    (left, top, width, height) in screen coordinates.
    """
    if getattr(cfg, "target_mode", "region") != "window":
        region = cfg.region
        return region.left, region.top, region.width, region.height

    handle = find_window(cfg.target_window_title, cfg.target_window_process)
    if not handle:
        return 0, 0, 0, 0
    client = client_rect(handle)
    if client is None:
        return 0, 0, 0, 0
    client_x, client_y, client_width, client_height = client
    region = cfg.region

    left = max(0, region.left)
    top = max(0, region.top)
    width = region.width
    height = region.height

    if left >= client_width or top >= client_height:
        # The stored offset points outside the window: the window was resized after the
        # selection, or the offset is really a screen coordinate from the other mode.
        # The old code clamped the *size* to zero instead, which reported "目标窗口不可用"
        # and stopped translating with the window sitting right there on screen. Follow
        # the whole content area rather than capture nothing.
        left, top = 0, 0
        width, height = client_width, client_height
    else:
        # Clamp so a shrunken window cannot ask for pixels that are not there.
        width = max(0, min(width, client_width - left))
        height = max(0, min(height, client_height - top))

    return client_x + left, client_y + top, width, height


def target_window_label(cfg) -> str:
    """A human-readable description of what is being followed, for the status line."""
    if getattr(cfg, "target_mode", "region") != "window":
        return ""
    handle = find_window(cfg.target_window_title, cfg.target_window_process)
    if not handle:
        return f"找不到窗口「{cfg.target_window_title}」"
    for info in list_windows():
        if info.handle == handle:
            return info.label
    return cfg.target_window_title
