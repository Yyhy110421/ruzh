"""Entry point for the realtime screen translator.

Run with ``python main.py`` (or the bundled ``启动.bat``). Bootstrap must happen
before anything imports onnxruntime, hence the ordering here.
"""

from __future__ import annotations

import os
import sys
import traceback

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import bootstrap  # noqa: E402

bootstrap.prepare()


def _log_crash(exc: BaseException) -> str:
    from app import config as config_mod

    config_mod.LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = config_mod.LOG_DIR / "crash.log"
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n" + "=" * 60 + "\n")
        fh.write("".join(traceback.format_exception(exc)))
    return str(path)


def main() -> int:
    try:
        from app.ui import main as ui_main

        return ui_main()
    except Exception as exc:  # noqa: BLE001
        path = _log_crash(exc)
        try:
            import tkinter.messagebox as messagebox
            import tkinter as tk

            root = tk.Tk()
            root.withdraw()
            messagebox.showerror("启动失败", f"{exc}\n\n详细信息已写入：\n{path}")
            root.destroy()
        except Exception:  # noqa: BLE001
            print(f"启动失败：{exc}\n详细信息：{path}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
