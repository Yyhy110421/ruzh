"""Recognition must only re-run the strips whose pixels changed.

Measured on this machine: detecting a 1100x800 window costs ~1.3 s and recognising its 47
boxes another ~6 s at 128 ms each. So detection stays whole-frame - cheap, and it means a
text line straddling a strip boundary is still read in one piece - and only the crops from
changed strips go through the recogniser.

The engine is faked so this runs in milliseconds without a model.

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
from app.ocr_engine import OcrEngine  # noqa: E402

failures = 0


def check(ok, label, extra=""):
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


class FakeEngine:
    """Three boxes, one per strip, plus counters instead of a model."""

    def __init__(self):
        self.dets = 0
        self.rec_batches: list[int] = []

    def load_img(self, bgr):
        return bgr

    def text_det(self, img):
        self.dets += 1
        height = img.shape[0]
        boxes = []
        for fraction in (1 / 6, 1 / 2, 5 / 6):
            top = int(height * fraction)
            boxes.append(np.array([[10, top], [200, top], [200, top + 20], [10, top + 20]]))
        return (np.array(boxes), 0.0)

    def sorted_boxes(self, boxes):
        return list(boxes)

    def get_crop_img_list(self, img, boxes):
        return [img[int(b[0][1]):int(b[2][1]), 10:200] for b in boxes]

    def text_rec(self, crops):
        self.rec_batches.append(len(crops))
        return ([(f"row{len(self.rec_batches)}_{i}", 0.9) for i in range(len(crops))], 0.0)

    def __call__(self, bgr):
        return ([], 0.0)


def make():
    engine = OcrEngine(AppConfig().ocr, None, "en")
    engine._engine = FakeEngine()
    return engine


def frame(height=600, width=400, fill=255):
    return np.full((height, width, 3), fill, dtype=np.uint8)


def main() -> int:
    print("=" * 78)
    print("一、第一次：检测 1 次，识别 3 张")
    print("=" * 78)
    engine = make()
    first = engine.recognize_cached(frame())
    fake = engine._engine
    check(fake.dets == 1, f"只检测了一次：{fake.dets}")
    check(fake.rec_batches == [3], f"一次批量识别 3 张：{fake.rec_batches}")
    check(first.reused_strips == 0, f"复用 0 条：{first.reused_strips}")
    check(len(first.lines) == 3, f"3 行：{len(first.lines)}")
    print()

    print("=" * 78)
    print("二、同一画面：检测照跑，但一张都不再识别")
    print("=" * 78)
    before = len(fake.rec_batches)
    second = engine.recognize_cached(frame())
    check(fake.dets == 2, f"检测跑了第 2 次：{fake.dets}")
    check(len(fake.rec_batches) == before, f"没有再识别：{fake.rec_batches}")
    check(second.reused_strips == 3, f"复用 3 条：{second.reused_strips}")
    check([l.text for l in first.lines] == [l.text for l in second.lines], "结果完全一致")
    print()

    print("=" * 78)
    print("三、只改中间一条：只识别那一条里的框")
    print("=" * 78)
    changed = frame()
    changed[250:350, 20:380] = 0
    third = engine.recognize_cached(changed)
    check(fake.rec_batches[-1] == 1, f"只识别了 1 张：{fake.rec_batches[-1]}")
    check(third.reused_strips == 2, f"另外 2 条复用：{third.reused_strips}")
    check(len(third.lines) == 3, f"结果仍是 3 行：{len(third.lines)}")
    print()

    print("=" * 78)
    print("四、画面太矮不走分条")
    print("=" * 78)
    small = make()
    result = small.recognize_cached(frame(height=60))
    # The fallback still detects - it is a plain full read - but it must not have gone
    # through the strip path, so no strip cache is left behind.
    check(small._strip_cache == [], "没有走分条逻辑")
    check(result.reused_strips == 0, "报告没有复用任何条带")
    print()

    print("=" * 78)
    print("五、检测失败要老实退回整帧识别")
    print("=" * 78)
    broken = make()

    def boom(img):
        raise RuntimeError("检测挂了")

    broken._engine.text_det = boom
    result = broken.recognize_cached(frame())
    check(result is not None, "没有抛出去")
    check(result.lines == [], f"退回后是空结果：{len(result.lines)} 行")
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
