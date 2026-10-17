"""The attribution notice the product has to carry.

The bundled Chinese-Russian dictionary is CC BY-SA 3.0, and the offline route from Russian
to Chinese runs through a CC-BY-4.0 model. Both licences permit commercial use and both
require attribution. A notice sitting in app/data/README.md satisfies the letter of that
and nobody will ever see it, which is why the same notice is a tab in the settings window.

This test exists so that removing the notice is a test failure rather than something
discovered after the product has been sold.

Run: python tests/test_about.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "about.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import config as config_mod  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


#: Every one of these is a licence term, not decoration. Removing one is a compliance
#: problem, so each is pinned separately rather than trusting one word like "授权".
REQUIRED = [
    ("FreeDict", "词典来源"),
    ("WikDict", "词典上游"),
    ("156,590", "词条数"),
    ("Karl Bartel", "词典维护者"),
    ("CC BY-SA 3.0", "词典授权"),
    ("CC-BY-4.0", "模型授权"),
    ("opus-mt-ru-en", "俄中路线实际用到的模型"),
    ("RapidOCR", "OCR 组件"),
    ("wordfreq", "俄语词表来源"),
    ("MIT", "本项目许可"),
]


def main() -> int:
    config_mod.ensure_dirs()
    cfg = config_mod.load()

    from app.ui import App, SettingsWindow

    app = App(cfg)
    app.update()
    window = SettingsWindow(app)
    window.update()
    notebook = window.winfo_children()[0]

    labels = [notebook.tab(i, "text") for i in range(notebook.index("end"))]
    print("=" * 78)
    print("一、页签在不在")
    print("=" * 78)
    print(f"        页签：{labels}")
    check("关于" in labels, "有「关于」页")
    index = labels.index("关于") if "关于" in labels else -1

    if index >= 0:
        notebook.select(index)
        window.update()

    print()
    print("=" * 78)
    print("二、正文里该有的都在")
    print("=" * 78)
    box = None
    for widget in window.winfo_children():
        pass
    # The text widget lives under the selected tab's frame.
    tab = notebook.nametowidget(notebook.tabs()[index]) if index >= 0 else None
    if tab is not None:
        for child in tab.winfo_children():
            if isinstance(child, __import__("tkinter").Text):
                box = child
                break
    check(box is not None, "正文框在")

    body = box.get("1.0", "end") if box is not None else ""
    for needle, why in REQUIRED:
        check(needle in body, f"{why}：{needle}")

    print()
    print("=" * 78)
    print("三、按钮")
    print("=" * 78)
    texts: list[str] = []
    if tab is not None:
        for child in tab.winfo_children():
            for sub in child.winfo_children():
                try:
                    texts.append(str(sub.cget("text")))
                except Exception:  # noqa: BLE001
                    pass
    check(any("复制" in t for t in texts), f"有复制按钮：{texts}")

    print()
    print("=" * 78)
    print("四、署名文件真的随程序走")
    print("=" * 78)
    notices = config_mod.APP_DIR / "app" / "data" / "README.md"
    check(notices.is_file(), f"app/data/README.md 在（{notices}）")
    if notices.is_file():
        raw = notices.read_text(encoding="utf-8", errors="replace")
        check("CC BY-SA 3.0" in raw, "署名文件里写了词典授权")
        check("ShareAlike" in raw or "相同方式共享" in raw,
              "署名文件里讲清了相同方式共享的含义")

    app.destroy()

    print()
    print("=" * 78)
    print(f"失败 {failures} 项")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
