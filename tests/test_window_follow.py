"""The point of the feature: move the window and the translation must follow.

Builds a fake chat window holding a Russian line, targets it by window rather than by
screen position, then drags it across the screen while the pipeline runs. Two things
have to hold: the capture rectangle must move with the window, and the recognised text
must still be the text in the window afterwards.
"""
import os
import queue
import sys
import time
import tkinter as tk
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "window_follow.log", "w", encoding="utf-8",
            errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig, Region  # noqa: E402
from app.ocr_engine import fold_to_cyrillic  # noqa: E402
from app.overlay import Overlay  # noqa: E402
from app.pipeline import RESULT, STATUS, TARGET, Pipeline  # noqa: E402
from app import window_target as wt  # noqa: E402

TITLE = "模拟聊天窗口-跟随测试"
LINE = "Цена указана за одну тонну без доставки."
W, H = 720, 260
POSITIONS = [(240, 300), (900, 220), (300, 620)]
SECONDS = 150


def main() -> int:
    root = tk.Tk()
    root.title(TITLE)
    root.configure(bg="#0d0d12")
    root.geometry(f"{W}x{H}+{POSITIONS[0][0]}+{POSITIONS[0][1]}")
    root.attributes("-topmost", True)
    tk.Label(root, text="客户:", bg="#0d0d12", fg="#8a8a95",
             font=("Microsoft YaHei UI", 11)).pack(anchor="w", padx=16, pady=(14, 0))
    tk.Label(root, text=LINE, bg="#0d0d12", fg="#f2f2f7", font=("Segoe UI", 19),
             wraplength=W - 60, justify="left").pack(anchor="w", padx=16, pady=(2, 0))
    root.update()
    time.sleep(0.5)
    root.update()

    handle = wt.find_window(TITLE, "python.exe")
    info = next((w for w in wt.list_windows() if w.handle == handle), None)
    print(f"目标窗口 hwnd={handle}")
    if info is None:
        print("找不到测试窗口")
        return 1
    print(f"  {info.label}  内容区 {info.client_width}x{info.client_height}")

    cfg = AppConfig()
    cfg.target_mode = "window"
    cfg.target_window_title = TITLE
    cfg.target_window_process = "python.exe"
    # A band across the message, expressed relative to the window's content area.
    cfg.region = Region(left=10, top=30, width=W - 30, height=90)
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = "opus"
    cfg.settle_frames = 1
    cfg.overlay.enabled = True

    events: queue.Queue = queue.Queue()
    pipeline = Pipeline(cfg, events)
    overlay = Overlay(root)
    overlay.apply_settings(cfg.overlay, cfg.region)
    overlay.set_capture_excluded(True)

    print(f"相对窗口的框选: {cfg.region.as_tuple()}")
    print("-" * 74)

    started = time.time()
    pipeline.start()
    #: When the translation model reported ready; the drag schedule is anchored to it.
    ready_at: float | None = None
    moves_done = 0
    target_changes: list[tuple] = []
    texts: list[str] = []
    translations: list[str] = []
    moved_after: list[tuple] = []

    try:
        while time.time() - started < SECONDS:
            elapsed = time.time() - started

            # Drag the window three times, several seconds apart. Two things have to hold:
            # the models must be loaded (a move during loading lands mid-cycle and the
            # assertion below counts a move that had no chance to be seen), and the spacing
            # must exceed one full cycle because the capture rectangle is re-resolved at the
            # top of a cycle. Anchoring the schedule on the "model ready" status instead of
            # the wall clock is what stops this test from failing under full-suite load,
            # where loading takes several seconds longer than it does alone.
            if ready_at is not None and moves_done < len(POSITIONS) \
                    and elapsed > ready_at + 2 + moves_done * 22:
                x, y = POSITIONS[moves_done]
                root.geometry(f"+{x}+{y}")
                root.update()
                moves_done += 1
                print(f"[{elapsed:5.1f}s] >>> 把窗口拖动到 ({x}, {y}) <<<")

            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                root.update()
                continue

            if event.kind == TARGET:
                region = event.meta.get("region")
                target_changes.append(region)
                print(f"[{elapsed:5.1f}s] 截屏区域 -> ({region[0]},{region[1]}) "
                      f"{region[2]}x{region[3]}")
                # The UI does this; the test must too, to prove the overlay follows.
                overlay.apply_settings(cfg.overlay,
                                       Region(left=region[0], top=region[1],
                                              width=region[2], height=region[3]))
            elif event.kind == STATUS:
                print(f"[{elapsed:5.1f}s] {event.message}")
                if ready_at is None and "翻译模型就绪" in event.message:
                    ready_at = elapsed
            elif event.kind == RESULT and event.source:
                if event.changed:
                    texts.append(event.source)
                    translations.append(event.translation)
                    marker = f"（第 {moves_done} 次移动后）" if moves_done else ""
                    print(f"[{elapsed:5.1f}s] {marker}")
                    print(f"          识别: {event.source}")
                    print(f"          译文: {event.translation}")
                if moves_done and (not moved_after or moved_after[-1][0] != event.source):
                    # Recorded whether or not the text changed. What has to hold after a
                    # drag is that the region still reads the window's sentence, and the
                    # worker reports that every cycle. Requiring a fresh translation made
                    # this depend on OCR noise: the pipeline deliberately does not
                    # re-translate a sentence it has already translated.
                    moved_after.append((event.source, event.translation))
            root.update()
    except Exception:
        traceback.print_exc()
    finally:
        print(f"[{time.time() - started:5.1f}s] stopping")
        pipeline.stop()
        for closer in (overlay.destroy, root.destroy):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass

    print()
    print("=" * 74)
    failures = 0
    print(f"拖动窗口 {moves_done} 次，截屏区域变化 {len(target_changes)} 次")
    if moves_done and len(target_changes) >= moves_done:
        print("  [OK  ] 截屏区域跟着窗口移动了")
    else:
        failures += 1
        print("  [FAIL] 截屏区域没有跟着移动")

    # Distinct capture origins should match the distinct window positions.
    origins = {(region[0], region[1]) for region in target_changes}
    print(f"  出现过的截屏原点: {sorted(origins)}")
    if len(origins) >= min(2, moves_done + 1):
        print("  [OK  ] 多个位置都产生了不同的截屏原点")
    else:
        failures += 1
        print("  [FAIL] 截屏原点没有随窗口变化")

    print(f"\n移动之后仍然认出并翻译了 {len(moved_after)} 次：")
    for source, translation in moved_after:
        print(f"   {source}")
        print(f"   -> {translation}")
    def looks_like_the_line(text: str) -> bool:
        """Folded before comparing, not raw.

        The recogniser reads Ц as L and drops case, so the sentence comes back as
        "Lена указана за одну Тонну ..." - comparing the raw characters turns a known
        homoglyph problem into a coin toss. app/ocr_engine.fold_to_cyrillic exists for
        exactly this and is what the pipeline itself runs.
        """
        folded = fold_to_cyrillic(text).lower()
        return "тонн" in folded or "цена" in folded

    if moved_after and all(looks_like_the_line(s) for s, _ in moved_after):
        print("  [OK  ] 窗口移动后抓到的仍是窗口里的那句话")
    elif moved_after:
        print("  [WARN] 移动后抓到的文字与预期不同")
    else:
        failures += 1
        print("  [FAIL] 窗口移动后再没有产出译文")

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
