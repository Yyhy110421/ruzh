"""Recognition must reuse the strips that did not change.

A full 1100x800 window costs ~20 s to recognise here, and a chat window usually changes in
one place. recognize_cached() splits the frame at blank bands, re-reads only the strips that
changed, and reuses the rest. The strip boundaries must never cut a line of text in half.

The engine is stubbed so this runs in milliseconds and does not need a model.

Run: python tests/test_strip_reuse.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "strip_reuse.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

import numpy as np  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.ocr_engine import OcrEngine, OcrLine, OcrResult  # noqa: E402

failures = 0


def check(ok, label, extra=""):
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


class CountingEngine(OcrEngine):
    """Records each recognition and returns one line, without any model."""

    def __init__(self):
        super().__init__(AppConfig().ocr, None, "en")
        self._engine = object()          # pretend the sessions are loaded
        self.calls: list[int] = []

    def recognize(self, bgr):
        self.calls.append(bgr.shape[0])
        line = OcrLine(text=f"block{len(self.calls)}", score=1.0,
                       top=5.0, left=5.0, box=np.array([[5, 5], [60, 5], [60, 25], [5, 25]]))
        return OcrResult(lines=[line], text=line.text, elapsed=0.0)


def frame_with_bands(height=600, width=400):
    """White background with text-like dark rows and clean blank bands between them."""
    img = np.full((height, width, 3), 255, dtype=np.uint8)
    for row in range(20, height - 20, 40):
        img[row:row + 16, 20:width - 20] = 20
    return img


def main() -> int:
    print("=" * 78)
    print("一、空白带能被找到，且不切进文字里")
    print("=" * 78)
    engine = CountingEngine()
    frame = frame_with_bands()
    boundaries = engine._blank_boundaries(frame)
    check(boundaries is not None and len(boundaries) == 2,
          f"找到 2 条边界：{boundaries}")
    if boundaries:
        for cut in boundaries:
            band = frame[max(0, cut - 2):cut + 2]
            check(int(band.min()) > 200, f"边界 {cut} 落在空白处（最暗 {band.min()}）")
    print()

    print("=" * 78)
    print("二、第一次全读，第二次全复用")
    print("=" * 78)
    first = engine.recognize_cached(frame)
    check(len(engine.calls) == 3, f"第一次读了 3 条：{len(engine.calls)}")
    check(first.reused_strips == 0, f"第一次复用 0 条：{first.reused_strips}")
    before = len(engine.calls)
    second = engine.recognize_cached(frame)
    check(len(engine.calls) == before, f"第二次没有再调模型：{len(engine.calls) - before} 次")
    check(second.reused_strips == 3, f"复用 3 条：{second.reused_strips}")
    check([l.text for l in first.lines] == [l.text for l in second.lines],
          "两次结果完全一致")
    print()

    print("=" * 78)
    print("三、只改中间一条，只重读那一条")
    print("=" * 78)
    changed = frame.copy()
    # Inside the middle strip, clear of both cuts at 200 and 400: a change that touched a
    # boundary is supposed to move it, and then a full re-read is the correct answer.
    changed[232:352, 30:200] = 0
    before = len(engine.calls)
    third = engine.recognize_cached(changed)
    check(len(engine.calls) - before == 1,
          f"只多读了 1 条：{len(engine.calls) - before}")
    check(third.reused_strips == 2, f"另外 2 条复用：{third.reused_strips}")
    print()

    print("=" * 78)
    print("四、找不到空白带就老实全读")
    print("=" * 78)
    noise = np.random.default_rng(3).integers(0, 255, (600, 400, 3), dtype=np.uint8)
    check(engine._blank_boundaries(noise) is None, "满屏噪点 -> 没有安全边界")
    before = len(engine.calls)
    engine.recognize_cached(noise)
    check(len(engine.calls) - before == 1, "退回整帧读一次")
    print()

    print("=" * 78)
    print("五、太小的画面不走分条")
    print("=" * 78)
    tiny = np.full((60, 200, 3), 255, dtype=np.uint8)
    check(engine._blank_boundaries(tiny) is None, "60 像素高 -> 不分条")
    print()

    print("=" * 78)
    print("六、画面尺寸变了要重算边界、清缓存")
    print("=" * 78)
    engine2 = CountingEngine()
    engine2.recognize_cached(frame_with_bands(600, 400))
    edges_before = list(engine2._strip_edges or [])
    engine2.recognize_cached(frame_with_bands(900, 400))
    check(list(engine2._strip_edges or []) != edges_before,
          f"边界跟着重算了：{edges_before} -> {engine2._strip_edges}")
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
