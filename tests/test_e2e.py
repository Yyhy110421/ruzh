"""Dev e2e test with heartbeats so a stalled or dead pipeline is visible."""
import os
import queue
import sys
import threading
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_LOG = open(os.path.join(ROOT, "tests", "e2e_out.log"), "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

import tkinter as tk  # noqa: E402

from app.config import AppConfig, Region  # noqa: E402
from app.overlay import Overlay  # noqa: E402
from app.pipeline import ERROR, RESULT, STATS, STATUS, Event, Pipeline  # noqa: E402

SOURCE_LINE = "The quick brown fox jumps over the lazy dog."

#: How long to wait for the pipeline to recognise the line for the first time. Generous on
#: purpose: the models take about 3 seconds to load on an idle machine and have taken 9
#: when something else was using the CPU, and the old fixed 24-second timeline turned that
#: into a failure that had nothing to do with the code. The test waits for the work now
#: instead of for the clock.
READY_TIMEOUT = 120

#: How long to keep watching after the first read, so the overlay has a chance to leak.
WATCH_SECONDS = 12

#: Marker put in the overlay text so a leak can be detected unambiguously.
#:
#: Detecting "the OCR result contains CJK" is not a valid test: whatever else is on
#: screen (this chat window, for instance) may legitimately contain Chinese, and the
#: captured region can end up showing it. Only the overlay itself proves a leak.
OVERLAY_SENTINEL = "SENTINEL7K3"
OVERLAY_TEXT = f"敏捷的棕色狐狸跳过了懒狗。{OVERLAY_SENTINEL}"


def has_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def raise_window(root: tk.Tk) -> None:
    """Keep the source window above everything, including the chat window."""
    try:
        root.attributes("-topmost", True)
        root.lift()
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    root = tk.Tk()
    root.title("e2e source")
    root.configure(bg="#101014")
    root.geometry("820x150+320+360")
    root.attributes("-topmost", True)
    tk.Label(root, text=SOURCE_LINE, bg="#101014", fg="#F5F5F5",
             font=("Consolas", 26), wraplength=780, justify="left").pack(
        expand=True, fill="both", padx=16, pady=16)
    raise_window(root)
    root.update()
    time.sleep(0.5)
    root.update()

    x, y = root.winfo_rootx(), root.winfo_rooty()
    w, h = root.winfo_width(), root.winfo_height()
    region = Region(left=x, top=y, width=w, height=h)
    print(f"source window at ({x},{y}) size {w}x{h}")

    # Spelled out rather than left to the defaults. The source line below is English, and
    # the product's default language is Russian - so when that default changed, this test
    # silently started recognising English with the Cyrillic model and failing on a
    # perfectly good pipeline. Which default ships is test_langs.py's business; this test
    # is about capture, recognition, translation and the overlay working together.
    cfg = AppConfig(region=region, source_lang="en", target_lang="zh")
    cfg.interval_ms = 500

    events: queue.Queue = queue.Queue()
    pipeline = Pipeline(cfg, events)
    pipeline.start()

    overlay = Overlay(root)
    overlay.hide()

    events_seen = 0
    seen: list[str] = []
    overlay_shown = False
    started = time.time()
    last_beat = 0.0
    phase = 1

    ready_at = 0.0
    try:
        while True:
            elapsed = time.time() - started

            # Driven by the pipeline, not by the clock: phase 2 begins when the first line
            # has actually been recognised, whatever that took.
            if phase == 1 and seen:
                phase = 2
                ready_at = time.time()
                print(f"\n[{elapsed:5.1f}s] --- phase 2: overlay on top of the region ---")
                try:
                    overlay.apply_settings(cfg.overlay, cfg.region)
                    print("          capture excluded:", overlay.set_capture_excluded(True))
                    overlay.show_text("The quick brown fox", OVERLAY_TEXT)
                    overlay_shown = True
                except Exception:
                    traceback.print_exc()

            if phase == 2 and ready_at and time.time() - ready_at >= WATCH_SECONDS:
                break
            if phase == 1 and time.time() > started + READY_TIMEOUT:
                # Never got going at all. Fall through: the summary and the assertions
                # below are what say so, and they say it with the log attached.
                print(f"\n[{elapsed:5.1f}s] gave up waiting for a first read")
                break

            if elapsed - last_beat >= 3.0:
                last_beat = elapsed
                # Anything else on screen (this chat window, for instance) would both
                # cover the region and inject unrelated text into the OCR result.
                raise_window(root)
                print(f"[{elapsed:5.1f}s] heartbeat: pipeline_alive={pipeline.is_alive()} "
                      f"events={events_seen} root_exists={bool(root.winfo_exists())} "
                      f"threads={[t.name for t in threading.enumerate()]}")

            try:
                event = events.get(timeout=0.2)
            except queue.Empty:
                try:
                    root.update()
                except Exception:
                    traceback.print_exc()
                    break
                continue

            events_seen += 1
            if event.kind == STATUS:
                print(f"[{time.time() - started:5.1f}s] [status] {event.message}")
            elif event.kind == ERROR:
                print(f"[{time.time() - started:5.1f}s] [ERROR ] {event.message}")
                if event.meta.get("detail"):
                    print(event.meta["detail"])
            elif event.kind == RESULT and event.source:
                seen.append(event.source)
                print(f"[{time.time() - started:5.1f}s] [p{phase}] ocr={event.ocr_ms:5.0f}ms "
                      f"tr={event.translate_ms:6.0f}ms changed={event.changed}")
                print(f"          src: {event.source}")
                print(f"          dst: {event.translation}")
            try:
                root.update()
            except Exception:
                traceback.print_exc()
                break
    finally:
        print(f"[{time.time() - started:5.1f}s] stopping")
        pipeline.stop()
        try:
            overlay.destroy()
        except Exception:  # noqa: BLE001
            pass
        try:
            root.destroy()
        except Exception:  # noqa: BLE001
            pass

    print("\n=== summary ===")
    print("events:", events_seen, "distinct OCR reads:", len(set(seen)))
    for text in sorted(set(seen)):
        print("   -", text)

    if overlay_shown:
        leaked = sorted({t for t in seen if OVERLAY_SENTINEL in t})
        if leaked:
            print("\n[FAIL] overlay text leaked into OCR:", leaked)
            return 1
        print(f"\n[OK] overlay never appeared in the captured region "
              f"（哨兵串 {OVERLAY_SENTINEL} 未出现）")

    if not any("fox" in t.lower() for t in seen):
        print("\n[FAIL] never recognised the source line")
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
