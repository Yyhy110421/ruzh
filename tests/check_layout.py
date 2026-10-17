"""Is anything in the new UI laid out off-screen?

A screenshot showed the text tab without its action buttons, which would be a real
usability bug. Rather than guess from pixels, ask Tk where each widget ended up and
compare against the space its parent actually has.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "layout.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import config as config_mod  # noqa: E402


def report(label: str, widget, parent) -> None:
    widget.update_idletasks()
    try:
        height = widget.winfo_height()
        y = widget.winfo_y()
        parent_h = parent.winfo_height()
        bottom = y + height
        ok = height > 1 and bottom <= parent_h
        flag = "OK  " if ok else "HIDDEN"
        print(f"  [{flag}] {label:<22} y={y:4d} h={height:4d} "
              f"bottom={bottom:4d} 父高={parent_h:4d}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [ERR ] {label}: {exc}")


def main() -> int:
    config_mod.ensure_dirs()
    cfg = config_mod.load()
    cfg.translate.engine = "opus"
    cfg.region.width = cfg.region.height = 0

    from app.ui import App

    app = App(cfg)
    app.update()
    print(f"窗口: {app.winfo_width()}x{app.winfo_height()}")
    print()

    # screen tab
    app.notebook.select(0)
    app.update()
    screen_tab = app.notebook.nametowidget(app.notebook.tabs()[0])
    print(f"屏幕翻译页 (高度 {screen_tab.winfo_height()}):")
    for child in screen_tab.winfo_children():
        report(type(child).__name__, child, screen_tab)

    # text tab
    app.notebook.select(1)
    app.update()
    text_tab = app.notebook.nametowidget(app.notebook.tabs()[1])
    print(f"\n文本翻译页 (高度 {text_tab.winfo_height()}):")
    for child in text_tab.winfo_children():
        report(type(child).__name__, child, text_tab)

    print("\n状态栏:")
    statusbar = [c for c in app.winfo_children() if isinstance(c, type(app.notebook))
                 is False and c.winfo_class() == "TFrame"]
    for child in app.winfo_children():
        report(child.winfo_class(), child, app)

    app._on_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
