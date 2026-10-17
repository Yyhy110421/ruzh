"""Translating a PDF, digital pages and scanned ones in the same file.

A contract that was signed, scanned and then emailed has a digital first page and
photographed ones after it, so the decision is per page. tests/fixtures/contract.pdf is
such a file, committed rather than generated so that reading a PDF never needs the
library that wrote one:

    page 1  a real text layer - a heading and an amount
    page 2  nothing but a picture of the text, the way a scan arrives
    page 3  a text layer shorter than the threshold, like a signature block

Run: python tests/test_pdf_tool.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "pdf_tool.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.pdf_tool import (SCANNED_THRESHOLD, PdfError, open_document,  # noqa: E402
                          page_text, translate_pdf)

FIXTURE = ROOT / "tests" / "fixtures" / "contract.pdf"

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


class StubTranslator:
    """Stand-in for Translator: marks what it saw, no model involved."""

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


class StubOcr:
    """Stands in for OcrEngine: reports the lines it was told to."""

    def __init__(self, lines=("Отгрузка со склада.",)):
        self.lines = list(lines)
        self.calls = 0

    def recognize(self, _frame):
        from app.ocr_engine import OcrLine, OcrResult

        self.calls += 1
        items = [OcrLine(text=t, score=0.9) for t in self.lines]
        return OcrResult(lines=items, text=" ".join(self.lines), elapsed=0.0)


def page_texts(document) -> list:
    """Every page's text, closing each page as it goes.

    pdfium holds a decoded page until it is closed, and a test that forgets shows up as
    a warning on exit rather than as a failure.
    """
    texts = []
    for index in range(len(document)):
        page = document[index]
        try:
            texts.append(page_text(page))
        finally:
            page.close()
    return texts

def main() -> int:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sample = FIXTURE

        print("=" * 78)
        print("一、每一页怎么读，是逐页判断的")
        print("=" * 78)
        document = open_document(sample)
        check(len(document) == 3, f"三页：{len(document)}")
        first, second, third = page_texts(document)
        document.close()
        check("ДОГОВОР" in first, f"第 1 页有文字层：{first[:30]!r}")
        check(second == "", f"第 2 页没有文字层（是图）：{second!r}")
        check(0 < len(third) < SCANNED_THRESHOLD,
              f"第 3 页文字很短（{len(third)} 字），会被先当成扫描页")
        print()

        print("=" * 78)
        print("二、数字页走文字层，图片页走识别")
        print("=" * 78)
        translator = StubTranslator()
        ocr = StubOcr()
        result = translate_pdf(object(), "", sample, translator=translator, ocr=ocr)
        check(result.total_pages == 3, f"三页都翻了：{result.total_pages}")
        check(result.pages[0].from_ocr is False, "第 1 页用的是文字层")
        check(result.pages[1].from_ocr is True, "第 2 页走的是识别")
        # Two, not one: page 2 is a picture, and page 3 is short enough that it is tried
        # as a picture first and only then falls back to its text layer. Being wrong in
        # that direction costs a second; being wrong the other way loses the page.
        check(ocr.calls == 2, f"识别调了两次（第 2 页 + 第 3 页先试）：{ocr.calls}")
        # The text layer separates words with a no-break space, so without normalising it
        # "600 000" is not a number to the masker and the amount goes through the model.
        check("600 000" in result.pages[0].original,
              f"数字原样在原文里，空格是真的空格：{result.pages[0].original!r}")
        check("Отгрузка" in result.pages[1].original,
              f"第 2 页走的是识别结果：{result.pages[1].original!r}")
        check(all(page.translated.startswith("ZH:")
                  for page in result.pages), "每页都翻了")
        print()

        print("=" * 78)
        print("三、文字层太短但确实是数字页，不能丢")
        print("=" * 78)
        # A signature block is digital and worth twenty characters. Recognising the page
        # finds nothing, and dropping it would lose the signature line silently.
        blank_ocr = StubOcr(lines=())
        result2 = translate_pdf(object(), "", sample, translator=StubTranslator(),
                                ocr=blank_ocr)
        third_page = result2.pages[2]
        check(third_page.from_ocr is False, "第 3 页退回文字层")
        check("Подпись" in third_page.original,
              f"签名那行还在：{third_page.original!r}")
        check(result2.digital_pages == 2, f"数字页数 2：{result2.digital_pages}")
        check(result2.scanned_pages == 1, f"扫描页数 1：{result2.scanned_pages}")
        print()

        print("=" * 78)
        print("四、进度按页走")
        print("=" * 78)
        seen = []
        translate_pdf(object(), "", sample, translator=StubTranslator(), ocr=StubOcr(),
                      on_progress=lambda done, total: seen.append((done, total)))
        check(seen == [(1, 3), (2, 3), (3, 3)], f"三步走完：{seen}")
        print()

        print("=" * 78)
        print("五、坏文件说人话")
        print("=" * 78)
        not_pdf = tmp / "not-a-pdf.pdf"
        not_pdf.write_bytes(b"this is not a pdf at all")
        try:
            open_document(not_pdf)
            check(False, "坏文件应该报错")
        except PdfError as exc:
            check("打不开" in str(exc), f"报的是打不开：{str(exc)[:40]}")

        missing = tmp / "nope.pdf"
        try:
            open_document(missing)
            check(False, "不存在的文件应该报错")
        except PdfError as exc:
            check("找不到" in str(exc), f"报的是找不到：{str(exc)[:40]}")
        print()

        print("=" * 78)
        print("六、翻失败的那句留在原位")
        print("=" * 78)
        angry = StubTranslator(explode_on=["ПОСТАВКИ"])
        broken = translate_pdf(object(), "", sample, translator=angry,
                               ocr=StubOcr())
        check(broken.total_pages == 3, "页数没变")
        check(broken.failed == 1, f"记了一处失败：{broken.failed}")
        check("engine fell over" in broken.pages[0].translated,
              f"失败的那页写着原因：{broken.pages[0].translated!r}")
        check(broken.pages[1].translated.startswith("ZH:"), "别的页照常翻了")
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
