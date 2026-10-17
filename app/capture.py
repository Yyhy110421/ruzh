"""Screen capture for a fixed region.

``mss`` handles are cheap but not safe to share across threads, so one handle is
kept per thread and the returned buffer is copied (mss reuses its own buffers).
"""

from __future__ import annotations

import threading

import numpy as np

from .config import Region


class ScreenCapture:
    def __init__(self) -> None:
        self._local = threading.local()

    def _handle(self):
        handle = getattr(self._local, "sct", None)
        if handle is None:
            import mss

            handle = mss.mss()
            self._local.sct = handle
        return handle

    def reset(self) -> None:
        """Drop the calling thread's handle (call after a display change)."""
        handle = getattr(self._local, "sct", None)
        if handle is not None:
            try:
                handle.close()
            except Exception:  # noqa: BLE001
                pass
            self._local.sct = None

    def virtual_screen(self) -> dict:
        """Bounds covering every attached monitor, in physical pixels."""
        handle = self._handle()
        monitor = handle.monitors[0]
        return {
            "left": int(monitor["left"]),
            "top": int(monitor["top"]),
            "width": int(monitor["width"]),
            "height": int(monitor["height"]),
        }

    def monitors(self) -> list[dict]:
        handle = self._handle()
        out = []
        for mon in handle.monitors[1:]:
            out.append({
                "left": int(mon["left"]),
                "top": int(mon["top"]),
                "width": int(mon["width"]),
                "height": int(mon["height"]),
            })
        return out

    def grab(self, region: Region) -> np.ndarray:
        """Return the region as a BGR ``uint8`` array (what RapidOCR expects)."""
        if not region.valid:
            raise ValueError("截取区域无效")
        handle = self._handle()
        raw = handle.grab({
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
        })
        frame = np.asarray(raw, dtype=np.uint8)  # BGRA
        return np.ascontiguousarray(frame[:, :, :3])
