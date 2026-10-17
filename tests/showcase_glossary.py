"""Open the settings window on the glossary page, with a few rules filled in.

Left running so a screenshot can be taken: this is the visual check of the rule table,
which is the part a customer would actually work in.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import config as config_mod  # noqa: E402

#: A few terms a China-Russia trade user would pin down.
SAMPLE = [
    ("АКБ", "蓄电池"),
    ("спецодежда", "工作服"),
    ("без учёта доставки", "不含运费"),
    ("срок поставки", "交期"),
    ("предоплата", "预付款"),
    ("контейнер", "集装箱"),
    ("маркировка", "唛头"),
]


def main() -> int:
    config_mod.ensure_dirs()
    cfg = config_mod.load()
    cfg.translate.engine = "opus"
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.region.width = cfg.region.height = 0

    from app.glossary import Glossary, Rule

    path = Path(cfg.resolved_glossary_path())
    glossary = Glossary.load(path)
    glossary.replace_all([Rule(src=src, dst=dst, case_sensitive=False,
                               pairs=["ru->zh"]) for src, dst in SAMPLE])
    glossary.save(path)

    from app.ui import App, SettingsWindow

    app = App(cfg)
    app.update()
    window = SettingsWindow(app)
    window.update()
    # Narrow and hard against the left edge: the chat window occupies the right side of
    # this screen, and a screenshot has to show the table rather than the window's
    # top-left corner hiding behind it.
    window.geometry("560x700+0+40")
    window.winfo_children()[0].select(1)      # 词库 page
    window.update()
    window.lift()
    app.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
