"""Dev smoke test: construct the whole UI and exercise the main code paths."""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_LOG = open(os.path.join(ROOT, "tests", "smoke_out.log"), "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import Region, load  # noqa: E402
from app.pipeline import RESULT, STATS, STATUS, Event  # noqa: E402
from app.ui import App  # noqa: E402


def main() -> int:
    cfg = load()
    cfg.region = Region(left=80, top=80, width=640, height=140)
    cfg.overlay.click_through = True

    print("building App ...")
    app = App(cfg)
    app.withdraw()
    app.update()
    print("App built OK")

    app._refresh_region_label()
    app._update_model_label()
    app._apply_overlay_settings()
    app.update()
    print("overlay applied, capture_excluded =", app.overlay.capture_excluded)

    app.overlay.show_text("The quick brown fox.", "敏捷的棕色狐狸。")
    app.update()
    print("overlay text shown, geometry =", app.overlay.win.geometry() if app.overlay.win else None)
    app.overlay.hide()

    for event in (
        Event(kind=STATUS, message="测试状态"),
        Event(kind=RESULT, source="Hello world.", translation="你好，世界", changed=True,
              capture_ms=5, ocr_ms=120, translate_ms=800),
        Event(kind=STATS, capture_ms=5, ocr_ms=120, translate_ms=800),
    ):
        app._handle_event(event)
    app.update()
    print("event handling OK")

    print("source box :", repr(app.source_text.get("1.0", "end").strip()))
    print("result box :", repr(app.result_text.get("1.0", "end").strip()))
    print("stats label:", app.stats_var.get())
    print("log lines  :", len(app.log_text.get("1.0", "end").strip().splitlines()))

    app._on_close()
    print("closed OK")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
