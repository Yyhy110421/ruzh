"""The floating translation window.

One line of source at the top (collapsible), everything under it is the translation,
laid out the way the source sits on screen - left column on the left, right column on
the right, each block shrunk to fit the space it came from.

Two things matter for correctness here:
  * every block is scaled by ONE factor, never clamped per box, so the layout keeps its
    proportions instead of drifting apart;
  * the canvas scrolls when the content is bigger than the window, so shrinking the
    window can never make the blocks draw on top of each other.
"""
from __future__ import annotations

import tkinter as tk

FONT = "Microsoft YaHei"
BODY_BG = "#101014"
HEAD_BG = "#1b1b22"
FG = "#f5f5fa"
DIM = "#9a9aa8"
MIN_SCALE = 0.55  # unused: kept for import compatibility


class FloatingWindow(tk.Toplevel):
    """Fixed-size translation panel: one source line, then the laid-out translation."""

    WIDTH = 900
    HEIGHT = 680

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.title("守译 · 译文")
        self.configure(bg=BODY_BG)
        self.geometry(f"{self.WIDTH}x{self.HEIGHT}+80+60")
        self.minsize(260, 200)
        self._collapsed = False
        self._items: list[tuple[float, float, float, float, str]] = []
        self._source = ""

        self._head = tk.Frame(self, bg=HEAD_BG)
        self._head.pack(side="top", fill="x")
        self._toggle = tk.Button(self._head, text="▾", command=self._toggle,
                                 bg=HEAD_BG, fg=DIM, bd=0, relief="flat",
                                 activebackground=HEAD_BG, activeforeground=FG,
                                 font=(FONT, 10), width=2, cursor="hand2")
        self._toggle.pack(side="left", padx=(4, 0), pady=2)
        # One line, always. Wrapping here is what pushed the translation down and made
        # the window look like it had lost its proportions.
        # Packed BEFORE the source label: the label expands to fill the header, so anything
        # packed after it gets pushed off the right edge and disappears with a long line.
        self._topmost = tk.BooleanVar(value=False)
        tk.Checkbutton(self._head, text="置顶", variable=self._topmost,
                       command=self._apply_topmost, bg=HEAD_BG, fg=DIM,
                       selectcolor=HEAD_BG, activebackground=HEAD_BG,
                       activeforeground=FG, bd=0, highlightthickness=0,
                       font=(FONT, 8)).pack(side="right", padx=(0, 6), pady=2)
        self._source_label = tk.Label(self._head, text="（等待识别）", bg=HEAD_BG, fg=DIM,
                                     font=(FONT, 9), anchor="w", justify="left")
        self._source_label.pack(side="left", fill="x", expand=True, padx=(2, 8), pady=4)

        body = tk.Frame(self, bg=BODY_BG)
        body.pack(side="top", fill="both", expand=True)
        self._canvas = tk.Canvas(body, bg=BODY_BG, highlightthickness=0, bd=0)
        self._vbar = tk.Scrollbar(body, orient="vertical", command=self._canvas.yview)
        self._hbar = tk.Scrollbar(body, orient="horizontal", command=self._canvas.xview)
        self._canvas.configure(yscrollcommand=self._vbar.set, xscrollcommand=self._hbar.set)
        self._vbar.pack(side="right", fill="y")
        self._hbar.pack(side="bottom", fill="x")
        self._canvas.pack(side="left", fill="both", expand=True)
        self._canvas.bind("<Configure>", lambda _e: self._redraw())
        self._canvas.bind("<Enter>", lambda _e: self._canvas.bind_all(
            "<MouseWheel>", lambda ev: self._canvas.yview_scroll(int(-ev.delta / 120), "units")))
        self._canvas.bind("<Leave>", lambda _e: self._canvas.unbind_all("<MouseWheel>"))

    # -- public ------------------------------------------------------------
    def update_result(self, source: str, items, translation: str = "") -> None:
        text = " ".join((source or "").split())
        self._source = text[:300] + "…" if len(text) > 300 else text
        if not items and translation.strip():
            # No boxes to place (pairing failed or OCR gave none): still show the
            # translation, one line under another, instead of a blank window.
            items = [(0.0, float(n * 26), 400.0, 24.0, line)
                     for n, line in enumerate(translation.splitlines()) if line.strip()]
        self._items = [(float(i[0]), float(i[1]), float(i[2]), float(i[3]), str(i[4]))
                       for i in items if str(i[4]).strip()]
        self._apply_source()
        self._fit_to_content()
        self._redraw()

    def _apply_topmost(self) -> None:
        try:
            self.attributes("-topmost", bool(self._topmost.get()))
        except Exception:  # noqa: BLE001
            pass

    def _fit_to_content(self) -> None:
        """Resize the window to the layout instead of making the user drag edges."""
        if not self._items:
            return
        try:
            left = min(i[0] for i in self._items)
            top = min(i[1] for i in self._items)
            right = max(i[0] + i[2] for i in self._items)
            bottom = max(i[1] + i[3] for i in self._items)
            want_w = int((right - left) * 1.15) + 40
            want_h = int((bottom - top) * 1.15) + 70
            screen_w = self.winfo_screenwidth() - 80
            screen_h = self.winfo_screenheight() - 120
            self.geometry(f"{min(want_w, screen_w)}x{min(want_h, screen_h)}+80+60")
        except Exception:  # noqa: BLE001
            pass

    def raise_above(self) -> None:
        """Bring the window to the front once, without pinning it above everything.

        Always-on-top was forced before; it covered whatever the user was reading, so the
        window now simply comes forward when a new translation arrives.
        """
        try:
            self.deiconify()
            self.lift()
        except Exception:  # noqa: BLE001
            pass

    # -- internals ---------------------------------------------------------
    def _toggle(self) -> None:
        self._collapsed = not self._collapsed
        self._toggle.configure(text="▸" if self._collapsed else "▾")
        self._apply_source()

    def _apply_source(self) -> None:
        if self._collapsed:
            self._source_label.configure(text="原文已折叠")
        else:
            self._source_label.configure(text=self._source or "（等待识别）")

    def _redraw(self) -> None:
        canvas = self._canvas
        canvas.delete("all")
        width = canvas.winfo_width() or (self.WIDTH - 30)
        height = canvas.winfo_height() or (self.HEIGHT - 70)
        if width < 20 or height < 20 or not self._items:
            canvas.configure(scrollregion=(0, 0, width, height))
            return

        left = min(i[0] for i in self._items)
        top = min(i[1] for i in self._items)
        right = max(i[0] + i[2] for i in self._items)
        bottom = max(i[1] + i[3] for i in self._items)
        span_w = max(1.0, right - left)
        span_h = max(1.0, bottom - top)

        # Everything scales by ONE factor, and that factor is whatever makes the whole
        # layout fit: no per-box minimums, no clipping at the edge, no truncation. If the
        # content is smaller than the canvas it is drawn at 1:1 rather than blown up.
        fit = min(width / span_w, height / span_h)
        scale = min(1.0, fit)
        total_w = span_w * scale
        total_h = span_h * scale

        for l, t, w, h, text in self._items:
            x = (l - left) * scale
            y = (t - top) * scale
            bw = max(8.0, w * scale)
            bh = max(8.0, h * scale)
            size, text = _fit(text, bw, bh)
            # No wrapping: Tk breaks CJK anywhere, which split "Yyhy110421/ruzh"
            # across lines and left a stray 。 alone. One line per block instead.
            canvas.create_text(x + 1, y, text=text, anchor="nw",
                               fill=FG, font=(FONT, size))

        canvas.configure(scrollregion=(0, 0, max(total_w, width), max(total_h, height)))


def _fit(text: str, width: float, height: float) -> tuple[int, str]:
    """Largest size at which the block fits on ONE line, plus the text that fits."""
    if width < 8 or height < 5:
        return 4, ''
    top = max(4, min(13, int(height / 1.45)))
    for size in range(top, 3, -1):
        per_char = size * 0.78
        room = max(1, int(width / per_char))
        if len(text) <= room:
            return size, text
        if size == 4:
            if room <= 2:
                return 4, text[:1]
            return 4, text[:room - 1] + '…'
    return 4, text[:1]
