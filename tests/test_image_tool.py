"""Translating a picture, without a camera and without a model.

The pipeline is the part worth pinning down: a photograph of a contract wraps every
visual line, so recognising it and translating line by line cuts the sentences in half.
This test feeds the module a box of lines the way the recogniser would emit them and
checks what comes out the other side - paragraph boundaries, order, and what happens to
a sentence that fails.

Run: python tests/test_image_tool.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "image_tool.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.image_tool import MAX_LONG_EDGE, load_bgr, translate_image  # noqa: E402
from app.ocr_engine import OcrLine, OcrResult  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


class StubOcr:
    """Stands in for OcrEngine, returning lines it was given."""

    def __init__(self, lines):
        self._lines = [OcrLine(text=text, score=0.9) for text in lines]
        self.calls = 0

    def recognize(self, _frame):
        self.calls += 1
        joined = " ".join(line.text for line in self._lines)
        return OcrResult(lines=list(self._lines), text=joined, elapsed=0.0)


class StubTranslator:
    """Uppercases instead of translating, and remembers what it was asked."""

    def __init__(self, explode_on=()):
        self.seen = []
        self.explode_on = explode_on
        self.redacted_count = 0
        self.unrestored = []
        self.numbers_protected = 0
        self.numbers_unrestored = []

    def __call__(self, unit):
        self.seen.append(unit)
        for bad in self.explode_on:
            if bad in unit:
                raise RuntimeError("engine fell over")
        return "ZH:" + unit


def make_image(path, size, colour):
    from PIL import Image

    Image.new("RGB", size, colour).save(path)


def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)

        print("=" * 78)
        print("一、图片读得进来")
        print("=" * 78)
        red = tmp / "red.png"
        make_image(red, (200, 100), (255, 0, 0))
        frame, scaled = load_bgr(red)
        check(frame.shape == (100, 200, 3), f"形状 (100, 200, 3)：{frame.shape}")
        # The recogniser and OpenCV both expect BGR. A red pixel must arrive as
        # [0, 0, 255]; channel order silently swapped is the classic way to get
        # nonsense out of a model that is otherwise fine.
        check(tuple(int(v) for v in frame[50, 100]) == (0, 0, 255),
              f"通道是 BGR：{tuple(int(v) for v in frame[50, 100])}")
        check(not scaled, "小图没被缩放")

        huge = tmp / "huge.png"
        make_image(huge, (MAX_LONG_EDGE + 700, 400), (10, 20, 30))
        big_frame, big_scaled = load_bgr(huge)
        check(big_scaled, "超长边被缩放了")
        check(max(big_frame.shape[:2]) <= MAX_LONG_EDGE,
              f"缩放后最长边 {max(big_frame.shape[:2])} <= {MAX_LONG_EDGE}")
        # Aspect ratio has to survive, or text is stretched and recognition degrades.
        want = 400 * (MAX_LONG_EDGE / (MAX_LONG_EDGE + 700))
        check(abs(big_frame.shape[0] - want) <= 2,
              f"宽高比保住了（高 {big_frame.shape[0]}，期望约 {want:.0f}）")

        # A real file, because translate_image always reads one: the recogniser is
        # stubbed out below, but the picture is not.
        blank_png = tmp / "blank.png"
        make_image(blank_png, (60, 40), (255, 255, 255))
        print()

        print("=" * 78)
        print("二、被硬折行切断的句子要接回去")
        print("=" * 78)
        # This is the whole point. A contract photographed on a phone arrives like
        # this, and translating each visual line separately produces Russian-level
        # gibberish: half a sentence in, half a sentence out.
        lines = [
            "Настоящий договор заключён между ООО Ромашка",
            "и ООО Лютик о поставке товара",
            "на сумму 600 000 рублей.",
            "Срок поставки 30 дней.",
            "Оплата по факту.",
        ]
        stub = StubTranslator()
        result = translate_image(object(), "", blank_png, ocr=StubOcr(lines),
                                 translator=stub)
        check(len(result.lines) == 3,
              f"5 行折行文本合成 3 段：{len(result.lines)}")
        first = result.lines[0].original if result.lines else ""
        check("товара" in first and "600 000" in first,
              "被切断的那句完整落在一段里", repr(first))
        check(all(line.translated.startswith("ZH:") for line in result.lines),
              "每段都翻了")
        joined = result.original_text
        check(joined.index("Ромашка") < joined.index("Срок"),
              "阅读顺序没乱")
        print(f"        段落 1：{first}")
        print()

        print("=" * 78)
        print("三、一句翻失败，位置还在")
        print("=" * 78)
        # A contract with a silent gap in the middle is worse than one with a marker
        # saying which sentence failed. The paragraph must keep its place.
        angry = StubTranslator(explode_on=["Оплата"])
        broken = translate_image(object(), "", blank_png, ocr=StubOcr(lines),
                                 translator=angry)
        check(len(broken.lines) == 3, f"段落数没变：{len(broken.lines)}")
        check(broken.failed == 1, f"记了 1 处失败：{broken.failed}")
        tail_text = broken.lines[-1].translated if broken.lines else ""
        check("engine fell over" in tail_text,
              "失败的那段写着原因，没被吞掉", repr(tail_text))
        check(broken.lines[0].translated.startswith("ZH:"),
              "别的段照常翻了")
        print()

        print("=" * 78)
        print("四、图里没字，不能崩")
        print("=" * 78)
        blank = translate_image(object(), "", blank_png, ocr=StubOcr([]),
                                translator=StubTranslator())
        check(blank.empty, "报告为空")
        check(blank.paragraphs == 0, f"段落数 0：{blank.paragraphs}")
        check(blank.original_text == "" and blank.translated_text == "", "两个文本都是空的")
        print()

        print("=" * 78)
        print("五、脱敏和数字的计数要传出来")
        print("=" * 78)
        # A picture of a contract goes through exactly the same masking as pasted text.
        # If the count were dropped here the interface would say nothing was hidden,
        # which is the one thing it must never claim wrongly.
        counted = StubTranslator()
        counted.redacted_count = 3
        counted.unrestored = ["[[C2]]"]
        counted.numbers_protected = 4
        counted.numbers_unrestored = ["600 000"]
        tallied = translate_image(object(), "", blank_png, ocr=StubOcr(lines),
                                  translator=counted)
        check(tallied.redacted == 3, f"脱敏 3 处：{tallied.redacted}")
        check(tallied.unrestored == ["[[C2]]"], f"没还原的占位符：{tallied.unrestored}")
        check(tallied.numbers_protected == 4, f"数字保护 4 个：{tallied.numbers_protected}")
        check(tallied.numbers_unrestored == ["600 000"],
              f"丢掉的数字：{tallied.numbers_unrestored}")
        print()

        print("=" * 78)
        print("六、进度回调按句子数走")
        print("=" * 78)
        seen = []
        translate_image(object(), "", blank_png, ocr=StubOcr(lines),
                        translator=StubTranslator(),
                        on_progress=lambda done, total: seen.append((done, total)))
        check(bool(seen), f"回调被调了 {len(seen)} 次")
        check(all(total == seen[-1][1] for _, total in seen),
              "总数一直没变")
        check(seen[-1][0] == seen[-1][1],
              f"最后一步等于总数：{seen[-1] if seen else None}")
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
