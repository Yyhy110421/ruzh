"""Global hotkeys via Win32 ``RegisterHotKey``.

Tk cannot see keystrokes while another application has focus, so hotkeys are
served by a dedicated thread that pumps its own Win32 message queue.
"""

from __future__ import annotations

import ctypes
import queue
import threading
from ctypes import wintypes

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

_NAMED = {
    "SPACE": 0x20, "TAB": 0x09, "ENTER": 0x0D, "RETURN": 0x0D, "ESC": 0x1B, "ESCAPE": 0x1B,
    "BACKSPACE": 0x08, "INSERT": 0x2D, "DELETE": 0x2E, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "LEFT": 0x25, "UP": 0x26, "RIGHT": 0x27, "DOWN": 0x28,
    "`": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA,
    "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF,
}
for _i in range(1, 25):
    _NAMED[f"F{_i}"] = 0x6F + _i


def parse_hotkey(spec: str) -> tuple[int, int] | None:
    """Turn ``"Ctrl+Alt+T"`` / ``"F9"`` into (modifiers, virtual-key code)."""
    if not spec:
        return None
    mods = 0
    key: str | None = None
    for raw in spec.replace("_", "+").split("+"):
        part = raw.strip().upper()
        if not part:
            continue
        if part in ("CTRL", "CONTROL"):
            mods |= MOD_CONTROL
        elif part == "ALT":
            mods |= MOD_ALT
        elif part == "SHIFT":
            mods |= MOD_SHIFT
        elif part in ("WIN", "SUPER", "META"):
            mods |= MOD_WIN
        else:
            key = part
    if key is None:
        return None
    if key in _NAMED:
        vk = _NAMED[key]
    elif len(key) == 1 and (key.isalpha() or key.isdigit()):
        vk = ord(key)
    elif key.startswith("F") and key[1:].isdigit() and 1 <= int(key[1:]) <= 24:
        vk = 0x6F + int(key[1:])
    else:
        return None
    return mods | MOD_NOREPEAT, vk


class HotkeyManager(threading.Thread):
    """Registers hotkeys and pushes their names onto a queue."""

    def __init__(self, mapping: dict[str, str], out: queue.Queue) -> None:
        super().__init__(name="hotkeys", daemon=True)
        self.mapping = dict(mapping)
        self.out = out
        self.registered: dict[str, str] = {}
        self.failed: dict[str, str] = {}
        self._stop = threading.Event()
        self._thread_id: int | None = None
        self._ready = threading.Event()

    def run(self) -> None:
        user32 = ctypes.windll.user32
        self._thread_id = int(ctypes.windll.kernel32.GetCurrentThreadId())

        ids: dict[int, str] = {}
        next_id = 1
        for name, spec in self.mapping.items():
            parsed = parse_hotkey(spec)
            if parsed is None:
                self.failed[name] = f"无法解析按键「{spec}」"
                continue
            mods, vk = parsed
            if user32.RegisterHotKey(None, next_id, mods, vk):
                ids[next_id] = name
                self.registered[name] = spec
                next_id += 1
            else:
                self.failed[name] = f"「{spec}」已被其它程序占用"
        self._ready.set()

        msg = wintypes.MSG()
        while not self._stop.is_set():
            got = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if got in (0, -1):
                break
            if msg.message == WM_HOTKEY:
                name = ids.get(int(msg.wParam))
                if name:
                    try:
                        self.out.put_nowait(name)
                    except queue.Full:
                        pass

        for hotkey_id in ids:
            user32.UnregisterHotKey(None, hotkey_id)

    def wait_ready(self, timeout: float = 3.0) -> bool:
        return self._ready.wait(timeout)

    def stop(self) -> None:
        self._stop.set()
        if self._thread_id:
            try:
                ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:  # noqa: BLE001
                pass
