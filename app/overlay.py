"""The floating subtitle window.

Two Win32 details matter here:

``SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)``
    The window is drawn on screen but omitted from screen captures. Without it, an
    overlay sitting on top of the selected region would be photographed by the next
    cycle and OCR would read its own translation back.

``WS_EX_TRANSPARENT | WS_EX_NOACTIVATE``
    Clicks fall through to the game or player underneath and the overlay never
    steals focus.
"""

from __future__ import annotations

import ctypes
import tkinter as tk
from tkinter import font as tkfont

from .config import OverlaySettings, Region

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_TOOLWINDOW = 0x00000080

WDA_NONE = 0x00000000
WDA_EXCLUDEFROMCAPTURE = 0x00000011

#: A colour that will not appear in real subtitles, used as the transparent key.
CHROMA_KEY = "#0b0c0d"


class Overlay:
    def __init__(self, parent: tk.Misc) -> None:
        self.parent = parent
        self.win: tk.Toplevel | None = None
        self._frame: tk.Frame | None = None
        self._source_label: tk.Label | None = None
        self._text_label: tk.Label | None = None
        self._settings = OverlaySettings()
        self._region: Region | None = None
        self._click_through = False
        self._capture_excluded = False
        self._dragging = False
        self._drag_offset = (0, 0)
        self._manual_pos: tuple[int, int] | None = None
        self.last_text = ""

    # -- window plumbing ---------------------------------------------------
    def _ensure_window(self) -> tk.Toplevel:
        if self.win is not None and self.win.winfo_exists():
            return self.win
        win = tk.Toplevel(self.parent)
        self.win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-toolwindow", True)
        except Exception:  # noqa: BLE001
            pass
        win.withdraw()

        frame = tk.Frame(win, bd=0, highlightthickness=1, highlightbackground="#3f3f46")
        frame.pack(fill="both", expand=True)
        self._frame = frame

        self._source_label = tk.Label(frame, justify="center", anchor="center")
        self._text_label = tk.Label(frame, justify="center", anchor="center")

        for widget in (frame, self._source_label, self._text_label):
            widget.bind("<ButtonPress-1>", self._start_drag)
            widget.bind("<B1-Motion>", self._do_drag)
            widget.bind("<ButtonRelease-1>", self._end_drag)
        return win

    def _hwnd(self) -> int | None:
        if self.win is None:
            return None
        try:
            hwnd = ctypes.windll.user32.GetAncestor(self.win.winfo_id(), 2)  # GA_ROOT
            return int(hwnd or self.win.winfo_id())
        except Exception:  # noqa: BLE001
            return None

    def _apply_ex_style(self, flags: int, enable: bool) -> None:
        hwnd = self._hwnd()
        if not hwnd:
            return
        user32 = ctypes.windll.user32
        try:
            getter = user32.GetWindowLongPtrW
            setter = user32.SetWindowLongPtrW
        except AttributeError:
            getter, setter = user32.GetWindowLongW, user32.SetWindowLongW
        try:
            current = getter(hwnd, GWL_EXSTYLE)
            updated = (current | flags) if enable else (current & ~flags)
            setter(hwnd, GWL_EXSTYLE, updated)
        except Exception:  # noqa: BLE001
            pass

    def set_click_through(self, enabled: bool) -> None:
        self._click_through = enabled
        self._apply_ex_style(
            WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_LAYERED | WS_EX_TOOLWINDOW, enabled
        )

    def set_capture_excluded(self, enabled: bool) -> bool:
        """Hide (or reveal) the window from screen captures. Returns success."""
        hwnd = self._hwnd()
        if not hwnd:
            return False
        try:
            ok = bool(ctypes.windll.user32.SetWindowDisplayAffinity(
                hwnd, WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
            ))
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            self._capture_excluded = enabled
        return ok

    @property
    def capture_excluded(self) -> bool:
        return self._capture_excluded

    # -- drag support (only when click-through is off) ---------------------
    def _start_drag(self, event: tk.Event) -> None:
        if self._click_through or self.win is None:
            return
        self._dragging = True
        self._drag_offset = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def _do_drag(self, event: tk.Event) -> None:
        if not self._dragging or self.win is None:
            return
        x = event.x_root - self._drag_offset[0]
        y = event.y_root - self._drag_offset[1]
        self.win.geometry(f"+{x}+{y}")
        self._manual_pos = (x, y)

    def _end_drag(self, _event: tk.Event) -> None:
        self._dragging = False

    def reset_position(self) -> None:
        self._manual_pos = None

    # -- content -----------------------------------------------------------
    def apply_settings(self, settings: OverlaySettings, region: Region | None) -> None:
        self._settings = settings
        self._region = region
        win = self._ensure_window()
        cfg = settings

        if cfg.background_mode == "transparent":
            bg = CHROMA_KEY
            try:
                win.attributes("-transparentcolor", CHROMA_KEY)
            except Exception:  # noqa: BLE001
                pass
            try:
                win.attributes("-alpha", 1.0)
            except Exception:  # noqa: BLE001
                pass
        else:
            bg = cfg.bg_color
            try:
                win.attributes("-transparentcolor", "")
            except Exception:  # noqa: BLE001
                pass
            try:
                win.attributes("-alpha", max(0.15, min(1.0, cfg.bg_opacity)))
            except Exception:  # noqa: BLE001
                pass

        for widget in (win, self._frame, self._source_label, self._text_label):
            if widget is not None:
                widget.configure(bg=bg)
        self._frame.configure(highlightbackground=bg)

        self._source_label.configure(
            fg="#A1A1AA",
            font=(cfg.font_family, max(9, int(cfg.font_size * 0.6))),
        )
        self._text_label.configure(
            fg=cfg.text_color,
            font=(cfg.font_family, cfg.font_size, "bold"),
        )
        self.set_click_through(cfg.click_through)

    def show_text(self, source: str, translation: str) -> None:
        cfg = self._settings
        win = self._ensure_window()
        body = (translation or "").strip()
        if not body:
            self.hide()
            return

        self.last_text = body
        if cfg.show_source and source.strip():
            self._source_label.configure(text=source.strip())
            if not self._source_label.winfo_ismapped():
                self._source_label.pack(side="top", fill="x", padx=cfg.padding, pady=(cfg.padding, 2))
        else:
            self._source_label.pack_forget()

        self._text_label.configure(text=body)
        self._text_label.pack(side="top", fill="both", expand=True,
                               padx=cfg.padding, pady=(2 if cfg.show_source else cfg.padding, cfg.padding))

        self._position_and_size()
        win.deiconify()
        win.lift()
        self.set_click_through(cfg.click_through)

    def _desired_size(self) -> tuple[int, int]:
        cfg = self._settings
        region = self._region
        if region is not None and region.valid:
            width = max(220, region.width)
        else:
            width = 720
        screen_w = self.parent.winfo_screenwidth()
        width = min(width, int(screen_w * 0.9))
        wrap = max(160, width - 2 * cfg.padding - 4)
        self._text_label.configure(wraplength=wrap)
        self._source_label.configure(wraplength=wrap)

        self.win.update_idletasks()
        height = self._frame.winfo_reqheight()
        max_height = int(cfg.font_size * 1.6 * max(1, cfg.max_lines)) + 2 * cfg.padding
        height = max(int(cfg.font_size * 1.8), min(height, max_height))
        return width, height

    def _position_and_size(self) -> None:
        cfg = self._settings
        region = self._region
        width, height = self._desired_size()
        if self._manual_pos is not None:
            x, y = self._manual_pos
        elif region is not None and region.valid:
            if cfg.position == "above":
                x, y = region.left, region.top - height - 6
            elif cfg.position == "below":
                x, y = region.left, region.top + region.height + 6
            else:  # cover
                x, y = region.left, region.top
        else:
            x = (self.parent.winfo_screenwidth() - width) // 2
            y = int(self.parent.winfo_screenheight() * 0.78)
        self.win.geometry(f"{width}x{height}+{x}+{y}")

    def hide(self) -> None:
        if self.win is not None and self.win.winfo_exists():
            self.win.withdraw()
        self.last_text = ""

    def destroy(self) -> None:
        if self.win is not None:
            try:
                self.win.destroy()
            except Exception:  # noqa: BLE001
                pass
            self.win = None
