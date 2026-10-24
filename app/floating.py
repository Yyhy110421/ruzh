"""The floating translation window.

One line of source at the top (collapsible), everything under it is the translation,
laid out the way the source sits on screen - left column on the left, right column on
the right, each block shrunk to fit the space it came from. It lives in its own fixed
box instead of covering the whole screen.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

FONT = "Microsoft YaHei"
BODY_BG = "#101014"
HEAD_BG = "#1b1b22"
FG = "#f2f2f7"
DIM = "#9a9aa8"


class FloatingWindow(tk.Toplevel):
    """Fixed-size translation panel: one source line, then the laid-out translation."""

    WIDTH = 460
    HEIGHT = 520

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.title("守译 · 译文")
        self.attributes("-topmost", True)
        self.configure(bg=BODY_BG)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+120+120")
        self.minsize(280, 220)
        self._collapsed = False
        self._items: list[tuple[float, float, float, float, str]] = []
        self._source = ""

        head = tk.Frame(self, bg=HEAD_BG)
        head.pack(side="top", fill="x")
        self._toggle = tk.Button(head, text="▾", command=self._toggle, bg=HEAD_BG, fg=DIM,
                                 bd=0, relief="flat", activebackground=HEAD_BG,
                                 activeforeground=FG, font=(FONT, 10), cursor="hand2")
        self._toggle.pack(side="left", padx=(6, 2), pady=4)
        self._source_var = tk.StringVar(value="（等待识别）")
        tk.Label(head, textvariable=self._source_var, bg=HEAD_BG, fg=DIM,
                 font=(FONT, 9), anchor="w", justify="left").pack(
            side="left", fill="x", expand=True, padx=(0, 8), pady=4)

        self._canvas = tk.Canvas(self, bg=BODY_BG, highlightthickness=0, bd=0)
        self._canvas.pack(side="top", fill="both", expand=True, padx=6, pady=6)
        self._canvas.bind("<Configure>", lambda _e: self._redraw())

    # -- public ------------------------------------------------------------
    def update_result(self, source: str, items) -> None:
        self._source = " ".join((source or "").split())
        if len(self._source) > 120:
            self._source = self._source[:120] + "…"
        self._source_var.set(self._source or "（等待识别）")
        self._items = [(float(i[0]), float(i[1]), float(i[2]), float(i[3]), str(i[4]))
                       for i in items if str(i[4]).strip()]
        self._redraw()

    def raise_above(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.attributes("-topmost", True)
        except Exception:  # noqa: BLE001
            pass

    # -- internals ---------------------------------------------------------
    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        if self._collapsed:
            self._source_var.set("原文已折叠（点 ▸ 展开）")
            self._toggle.configure(text="▸")
            self._saved = self._source
        else:
            self._source_var.set(getattr(self, "_saved", "") or self._source)
            self._toggle.configure(text="▾")
        self._redraw()

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        width = canvas.winfo_width() or (self.WIDTH - 20)
        height = canvas.winfo_height() or (self.HEIGHT - 60)
        if width < 30 or height < 30 or not self._items:
            return
        boxes, total_w, total_h = self._scaled(width, height)
        for x, y, w, h, text in boxes:
            size = _fit_size(text, w, h)
            canvas.create_text(
                x + 2, y + 1, text=text, anchor="nw", width=max(20, int(w)),
                fill=FG, font=(FONT, size),
                tags=("body",))

    def _scaled(self, width: int, height: int):
        """Map the captured boxes proportionally into the canvas."""
        left = min(i[0] for i in self._items)
        top = min(i[1] for i in self._items)
        right = max(i[0] + i[2] for i in self._items)
        bottom = max(i[1] + i[3] for i in self._items)
        span_w = max(1.0, right - left)
        span_h = max(1.0, bottom - top)
        scale = min(width / span_w, height / span_h)
        boxes = []
        for l, t, w, h, text in self._items:
            boxes.append(((l - left) * scale, (t - top) * scale,
                          max(24.0, w * scale), max(14.0, h * scale), text))
        return boxes, span_w * scale, span_h * scale


def _fit_size(text: str, width: float, height: float) -> int:
    """Largest point size whose wrapped text still fits the box."""
    size = int(max(7, min(13, height * 0.9)))
    while size > 6:
        per_line = max(1, int(width / (size * 0.75)))
        lines = max(1, -(-len(text) // per_line))
        if lines * size * 1.35 <= height + 4:
            return size
        size -= 1
    return 6
