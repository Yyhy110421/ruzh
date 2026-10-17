"""Full-screen rubber-band region picker."""

from __future__ import annotations

import tkinter as tk
from typing import Callable

from .capture import ScreenCapture
from .config import Region

MIN_SIZE = 16


class RegionPicker:
    """Dims every monitor and lets the user drag out a rectangle."""

    def __init__(self, parent: tk.Misc, on_done: Callable[[Region | None], None]) -> None:
        self.parent = parent
        self.on_done = on_done
        self._win: tk.Toplevel | None = None
        self._canvas: tk.Canvas | None = None
        self._rect: int | None = None
        self._origin: tuple[int, int] | None = None
        self._bounds = {"left": 0, "top": 0, "width": 1, "height": 1}

    # -- construction ------------------------------------------------------
    def show(self) -> None:
        if self._win is not None:
            return
        try:
            self._bounds = ScreenCapture().virtual_screen()
        except Exception:  # noqa: BLE001
            self._bounds = {
                "left": 0,
                "top": 0,
                "width": self.parent.winfo_screenwidth(),
                "height": self.parent.winfo_screenheight(),
            }

        left, top = self._bounds["left"], self._bounds["top"]
        width, height = self._bounds["width"], self._bounds["height"]

        win = tk.Toplevel(self.parent)
        self._win = win
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        try:
            win.attributes("-alpha", 0.30)
        except Exception:  # noqa: BLE001
            pass
        win.geometry(f"{width}x{height}+{left}+{top}")
        win.configure(bg="#000000", cursor="crosshair")

        canvas = tk.Canvas(win, bg="#000000", highlightthickness=0, cursor="crosshair")
        canvas.pack(fill="both", expand=True)
        self._canvas = canvas

        canvas.create_text(
            width // 2, 40,
            text="拖动鼠标框选要翻译的区域      Esc 取消",
            fill="#FFFFFF", font=("Microsoft YaHei UI", 18),
        )
        self._hint = canvas.create_text(
            width // 2, 74, text="", fill="#9AE6B4", font=("Consolas", 13)
        )

        canvas.bind("<ButtonPress-1>", self._on_press)
        canvas.bind("<B1-Motion>", self._on_drag)
        canvas.bind("<ButtonRelease-1>", self._on_release)
        win.bind("<Escape>", lambda _e: self._finish(None))
        win.focus_force()
        try:
            win.grab_set()
        except Exception:  # noqa: BLE001
            pass

    # -- events ------------------------------------------------------------
    def _on_press(self, event: tk.Event) -> None:
        self._origin = (event.x, event.y)
        if self._rect is not None:
            self._canvas.delete(self._rect)
        self._rect = self._canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline="#4ADE80", width=2, fill="#1F2937",
        )

    def _on_drag(self, event: tk.Event) -> None:
        if self._origin is None or self._rect is None:
            return
        x0, y0 = self._origin
        self._canvas.coords(self._rect, x0, y0, event.x, event.y)
        self._canvas.itemconfigure(
            self._hint, text=f"{abs(event.x - x0)} x {abs(event.y - y0)}"
        )

    def _on_release(self, event: tk.Event) -> None:
        if self._origin is None:
            self._finish(None)
            return
        x0, y0 = self._origin
        x1, y1 = event.x, event.y
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        if right - left < MIN_SIZE or bottom - top < MIN_SIZE:
            self._finish(None)
            return
        region = Region(
            left=self._bounds["left"] + left,
            top=self._bounds["top"] + top,
            width=right - left,
            height=bottom - top,
        )
        self._finish(region)

    def _finish(self, region: Region | None) -> None:
        win = self._win
        self._win = None
        self._rect = None
        self._origin = None
        if win is not None:
            try:
                win.grab_release()
            except Exception:  # noqa: BLE001
                pass
            try:
                win.destroy()
            except Exception:  # noqa: BLE001
                pass
        self.on_done(region)
