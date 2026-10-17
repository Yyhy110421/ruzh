# -*- coding: utf-8 -*-
"""Does translating a real PDF work, digital page and scanned page alike?

The unit test uses stubs; this one loads the real recogniser and the real translator and
puts tests/fixtures/contract.pdf through both. It is the only place that answers what a
customer would ask: did it read the page, and did the money survive.

Run: python tests/eval_pdf_pipeline.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "pdf_pipeline.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

FIXTURE = ROOT / "tests" / "fixtures" / "contract.pdf"

failures = 0


def check(ok, label, extra=""):
    global failures
    if not ok:
        failures += 1
    print("  [" + ("OK  " if ok else "FAIL") + "] " + label
          + ("  " + extra if extra else ""))


def main() -> int:
    import time

    from app.config import AppConfig
    from app.pdf_tool import translate_pdf

    cfg = AppConfig()
    cfg.source_lang = "ru"
    cfg.target_lang = "zh"

    print("测试件：" + FIXTURE.name + "（1 页数字版 + 1 页扫描版 + 1 页签名）")
    print()

    started = time.perf_counter()
    result = translate_pdf(cfg, cfg.resolved_models_root(), FIXTURE)
    elapsed = time.perf_counter() - started

    print("=" * 78)
    print("原文")
    print("=" * 78)
    print(result.original_text)
    print()
    print("=" * 78)
    print("译文")
    print("=" * 78)
    print(result.translated_text)
    print()
    print("=" * 78)
    print("检查")
    print("=" * 78)
    check(result.total_pages == 3, "三页都在（" + str(result.total_pages) + "）")
    check(result.digital_pages == 2, "两页数字版（" + str(result.digital_pages) + "）")
    check(result.scanned_pages == 1, "一页扫描版（" + str(result.scanned_pages) + "）")
    check(result.failed == 0, "没有翻译失败的句子（" + str(result.failed) + "）")

    source = result.original_text
    target = result.translated_text
    for number in ("600 000", "30", "5"):
        check(number in source, "原文里有 " + number)
    for number in ("600000", "600 000"):
        if number in target:
            check(True, "译文保住了总额 " + number)
            break
    else:
        check(False, "译文保住了总额", target[:200])
    check("30" in target, "译文保住了 30")
    # The scanned page's number came through recognition, not a text layer, so it is the
    # one that can quietly disappear.
    check("45" in target, "译文保住了 45（扫描页的数字）")
    check("45" in result.pages[1].original,
          "扫描页认出了 45：" + repr(result.pages[1].original[:60]))

    if "Челябинск" in source:
        check("车里雅宾斯克" in target, "城市 Челябинск 译成 车里雅宾斯克")

    print()
    print("  用时 " + f"{elapsed:.1f}" + " 秒，脱敏 " + str(result.redacted)
          + " 处，数字保护 " + str(result.numbers_protected) + " 个")
    print()
    print("=" * 78)
    print("失败 " + str(failures) + " 项")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
