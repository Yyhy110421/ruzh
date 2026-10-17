"""Open the new UI on the text tab with a real trade sentence already translated.

Left running so a screenshot can be taken of the result: this doubles as the visual
check of the redesign and as proof the text-translation path works end to end.
"""
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import config as config_mod  # noqa: E402

SAMPLE = (
    "Здравствуйте! Подтверждаем получение вашего запроса.\n"
    "Цена за одну тонну составляет 450 долларов США без учёта доставки.\n"
    "Срок поставки — 30 дней с момента оплаты."
)


def main() -> int:
    config_mod.ensure_dirs()
    cfg = config_mod.load()
    cfg.translate.engine = "opus"
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    # A region that looks like a picked subtitle strip, so the card is filled in.
    cfg.region.left, cfg.region.top = 420, 620
    cfg.region.width, cfg.region.height = 960, 140
    cfg.overlay.enabled = True

    from app.ui import App

    app = App(cfg)
    app.update()

    # Show the screen tab - the one a new user lands on. Pass an argument to preview
    # the text tab instead.
    if "text" in sys.argv:
        app.notebook.select(1)
        app.src_var.set("俄语")
        app.tgt_var.set("中文")
        app._on_language_change()
        app.update()
        app.text_in.insert("1.0", SAMPLE)
        app.update()
        app.after(400, app.run_text_translation)

    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
