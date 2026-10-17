"""Start / pause / resume / stop, and the double-pipeline bug that hid behind them.

The complaint was that there was no way to pause and that stopping felt unresponsive.
Investigating turned up a worse problem: ``stop()`` dropped its reference to the worker
immediately, so the buttons said "stopped" while the thread was still winding down, and
pressing Start again launched a *second* pipeline loading a second copy of the models.

This drives the real state machine and asserts on it.
"""
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "control_flow.log", "w", encoding="utf-8",
            errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import config as config_mod  # noqa: E402
from app.config import Region  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    if condition:
        print(f"  [OK  ] {label}")
    else:
        failures += 1
        print(f"  [FAIL] {label}")


def pump(app, seconds: float) -> None:
    """Let Tk process its after() callbacks, which is how the UI does its work."""
    end = time.time() + seconds
    while time.time() < end:
        try:
            app.update()
        except tk.TclError:
            return
        time.sleep(0.02)


def button_state(button) -> str:
    return "disabled" if button.instate(["disabled"]) else "enabled"


def main() -> int:
    config_mod.ensure_dirs()
    cfg = config_mod.load()
    cfg.translate.engine = "opus"
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.target_mode = "region"
    # A real patch of screen, so the pipeline gets past its validity check.
    cfg.region = Region(left=200, top=400, width=400, height=90)
    cfg.interval_ms = 400

    from app.ui import App

    app = App(cfg)
    app.update()

    print("=" * 74)
    print("初始状态")
    print("=" * 74)
    check(app.pipeline is None, "没有在跑")
    check(button_state(app.start_btn) == "enabled", "「开始」可点")
    check(button_state(app.pause_btn) == "disabled", "「暂停」不可点（没在跑）")
    check(button_state(app.stop_btn) == "disabled", "「停止」不可点（没在跑）")

    print()
    print("=" * 74)
    print("开始")
    print("=" * 74)
    app.start()
    pump(app, 1.0)
    first = app.pipeline
    check(first is not None and first.is_alive(), "流水线线程活着")
    check(button_state(app.start_btn) == "disabled", "「开始」变灰")
    check(button_state(app.pause_btn) == "enabled", "「暂停」可点")
    check(button_state(app.stop_btn) == "enabled", "「停止」可点")

    print()
    print("=" * 74)
    print("重复点「开始」不能起第二个流水线")
    print("=" * 74)
    app.start()
    pump(app, 0.4)
    check(app.pipeline is first, "仍然是同一个流水线实例")

    print()
    print("=" * 74)
    print("暂停 / 继续")
    print("=" * 74)
    app.toggle_pause()
    pump(app, 0.4)
    check(first.paused, "流水线报告已暂停")
    check("继续" in app.pause_btn.cget("text"), f"按钮文字变成「继续」"
                                               f"（{app.pause_btn.cget('text')!r}）")
    check(app.pipeline is first, "暂停没有销毁流水线（模型留在内存里）")

    app.toggle_pause()
    pump(app, 0.4)
    check(not first.paused, "继续后 paused 清掉")
    check("暂停" in app.pause_btn.cget("text"), "按钮文字变回「暂停」")

    print()
    print("=" * 74)
    print("停止：必须等线程真的退出")
    print("=" * 74)
    app.stop()
    check(app.pipeline is first,
          "调用 stop 后仍持有引用（不能马上当作已停止）")
    check(first.stopping, "流水线进入停止中状态")
    check(button_state(app.start_btn) == "disabled",
          "线程还活着时「开始」不可点 —— 这正是双流水线 bug 的入口")
    check(button_state(app.stop_btn) == "disabled", "「停止」变灰，避免重复点")
    check("停止" in app.status_var.get(), f"状态栏提示：{app.status_var.get()!r}")

    print("  等待线程真的退出…（模型加载 + 队列排空可能几十秒）")
    deadline = time.time() + 120
    while time.time() < deadline and app.pipeline is not None:
        pump(app, 0.2)
    check(app.pipeline is None, "线程退出后引用被清空")
    # The reference is what gates Start. Clearing it while the thread is still winding
    # down is how a second pipeline gets started on top of a dying one - so assert the
    # thread is really gone, not merely that the reference was dropped.
    check(not first.is_alive(),
          "清空引用时线程必须真的退出（否则「开始」会起第二条流水线）")
    check(button_state(app.start_btn) == "enabled", "「开始」重新可点")
    check(button_state(app.stop_btn) == "disabled", "「停止」变灰")
    check(app.status_var.get() == "已停止", f"状态栏：{app.status_var.get()!r}")

    print()
    print("=" * 74)
    print("停止后可以重新开始")
    print("=" * 74)
    app.start()
    pump(app, 1.0)
    second = app.pipeline
    check(second is not None and second is not first, "起了新流水线")
    check(second is not None and second.is_alive(), "新流水线活着")
    app.stop()
    deadline = time.time() + 120
    while time.time() < deadline and app.pipeline is not None:
        pump(app, 0.2)
    check(app.pipeline is None, "第二次也能干净停掉")
    check(second is not None and not second.is_alive(),
          "第二次清空引用时线程也真的退出了")

    print()
    print("=" * 74)
    print("没有区域时不许开始")
    print("=" * 74)
    app.cfg.region = Region(left=0, top=0, width=0, height=0)
    original = __import__("tkinter.messagebox", fromlist=["messagebox"]).showinfo
    __import__("tkinter.messagebox", fromlist=["messagebox"]).showinfo = \
        lambda *a, **k: None
    try:
        app.start()
    finally:
        __import__("tkinter.messagebox", fromlist=["messagebox"]).showinfo = original
    pump(app, 0.3)
    check(app.pipeline is None, "无效区域时拒绝启动")

    app._on_close()
    print()
    print("=" * 74)
    print(f"失败 {failures} 项")
    print("=" * 74)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
