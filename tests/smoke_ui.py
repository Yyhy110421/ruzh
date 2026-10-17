"""Build the new UI and exercise it without a human, so layout errors surface here.

Constructs the app, walks both tabs, opens and closes the settings window, switches
language and engine, then closes cleanly. Anything that only fails on a click would
otherwise be found by the user instead.
"""
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "smoke_ui.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import config as config_mod  # noqa: E402

failures = 0


def step(label: str, fn) -> None:
    global failures
    try:
        fn()
        print(f"  [OK  ] {label}")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  [FAIL] {label}: {type(exc).__name__}: {exc}")
        traceback.print_exc()


def main() -> int:
    config_mod.ensure_dirs()
    cfg = config_mod.load()
    # Keep the smoke test from inheriting a stale region from the real config.
    cfg.region.width = 0
    cfg.region.height = 0

    from app.ui import App

    print("构建主窗口")
    app = App(cfg)
    step("主窗口构建", lambda: app.update())

    print("\n遍历两个标签页")
    for index, name in ((0, "屏幕翻译"), (1, "文本翻译")):
        step(f"切到「{name}」", lambda i=index: (app.notebook.select(i), app.update()))

    print("\n语言与引擎")
    step("切到 俄语 → 中文", lambda: (app.src_var.set("俄语"), app.tgt_var.set("中文"),
                                    app._on_language_change(), app.update()))
    step("交换方向", lambda: (app.swap_languages(), app.update()))
    engine_labels = list(app.engine_box.cget("values"))
    print(f"        引擎列表共 {len(engine_labels)} 项：{engine_labels[:3]}…")
    step("切换到第二个引擎", lambda: (app.engine_var.set(engine_labels[1]),
                                     app._on_engine_change(), app.update()))
    step("切回离线引擎", lambda: (
        app.engine_var.set(next(l for l in engine_labels if l.startswith("离线"))),
        app._on_engine_change(), app.update()))

    print("\n设置窗口")
    from app.ui import SettingsWindow

    holder = {}

    def open_settings() -> None:
        window = SettingsWindow(app)
        holder["window"] = window
        for index in range(len(window.winfo_children()[0].tabs())):
            window.winfo_children()[0].select(index)
            window.update()
        window.append_log("冒烟测试写入一行日志")

    step("打开并遍历设置页", open_settings)
    step("关闭设置窗口", lambda: (holder["window"]._close(), app.update()))

    print("\n文本页动作")
    step("写入原文并清空", lambda: (
        app.text_in.insert("1.0", "Встреча перенесена."),
        app.clear_text(), app.update()))

    print("\n状态与日志")
    step("状态栏更新", lambda: app._status("冒烟测试"))
    step("无设置窗口时写日志不报错", lambda: app.log("没有设置窗口也应该安全"))

    print("\n关闭")
    step("正常关窗", lambda: app._on_close())

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
