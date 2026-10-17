"""Screen translation must keep the page's line structure, and not redo unchanged lines.

Two complaints drove this, and they have the same cause:

* "网站的内容是分行的但是翻译的译文是一大串根本分不出来哪里是哪里" - the whole recognised
  text was handed to the engine in one piece, so thirty short lines came back as one
  unbroken run.
* "翻译延迟太高" - and because it was one piece, a blinking cursor or one changing table
  cell meant retranslating everything.

The fix is one change: split at line breaks, merge only genuine wrapped continuations,
and cache each unit by its source text. This test pins all of it down without a model.

Run: python tests/test_pipeline_units.py
"""
import queue
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "pipeline_units.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


class StubRoute:
    """Stands in for the translation route, and counts what it was asked to do."""

    def __init__(self):
        self.seen = []

    def translate(self, text):
        self.seen.append(text)
        return "ZH:" + text


def make_pipeline():
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    return Pipeline(cfg, queue.Queue()), cfg

def main() -> int:
    pipeline, cfg = make_pipeline()
    route = StubRoute()

    print("=" * 78)
    print("一、分行的内容，译完还是分行的")
    print("=" * 78)
    lines = ["ДОГОВОР ПОСТАВКИ", "Цена 1200 рублей", "Срок поставки 30 дней"]
    out, ms, sent, _shown = pipeline._translate_units(lines, route, None, cfg)
    got = out.split("\n")
    check(len(got) == 3, f"三行进去，三行出来：{len(got)}", repr(out))
    check(sent == 3, f"送了三段：{sent}")
    check(got[0].endswith("ДОГОВОР ПОСТАВКИ"), f"第一行对得上：{got[0]!r}")
    print()

    print("=" * 78)
    print("二、被硬折行切断的句子，不能拆成两段")
    print("=" * 78)
    # A wrapped line is a continuation, not a new item. Splitting it would translate
    # half a sentence at a time, which is the opposite failure.
    wrapped = ["Настоящий договор заключён между ООО Ромашка",
               "и ООО Лютик о поставке товара."]
    route2 = StubRoute()
    out2, _, sent2, _ = pipeline._translate_units(wrapped, route2, None, cfg)
    check(sent2 == 1, f"两行合成一段送出去：{sent2}", repr(out2))
    check("Лютик" in out2, f"后半句在同一段里：{out2!r}")
    print()

    print("=" * 78)
    print("三、只有变的那一行重新翻（延迟问题的主因）")
    print("=" * 78)
    route3 = StubRoute()
    first = ["Строка один", "Строка два", "Строка три"]
    pipeline._translate_units(first, route3, None, cfg)
    check(len(route3.seen) == 3, f"第一遍翻了三段：{len(route3.seen)}")

    route3.seen.clear()
    second = ["Строка один", "Строка два ИЗМЕНИЛАСЬ", "Строка три"]
    out3, _, sent3, _ = pipeline._translate_units(second, route3, None, cfg)
    check(sent3 == 1, f"第二遍只翻了一段：{sent3}", f"实际请求 {route3.seen}")
    check(len(route3.seen) == 1, f"引擎只被调一次：{len(route3.seen)}")
    check("ИЗМЕНИЛАСЬ" in out3, "改的那行在结果里")
    check(out3.count("\n") == 2, f"结构没变：{out3!r}")
    print()

    print("=" * 78)
    print("四、换了语言/引擎，缓存必须作废")
    print("=" * 78)
    route4 = StubRoute()
    pipeline._translate_units(["Строка"], route4, None, cfg)
    route4.seen.clear()
    cfg.target_lang = "en"
    pipeline._translate_units(["Строка"], route4, None, cfg)
    check(len(route4.seen) == 1, f"换目标语言后重新翻了：{len(route4.seen)}")
    cfg.target_lang = "zh"
    route4.seen.clear()
    pipeline._translate_units(["Другой текст"], route4, None, cfg)
    check(len(route4.seen) == 1, "换回中文后也重新翻")
    print()

    print("=" * 78)
    print("五、空输入不炸")
    print("=" * 78)
    out5, ms5, sent5, _ = pipeline._translate_units([], StubRoute(), None, cfg)
    check(out5 == "" and sent5 == 0, f"空列表 -> 空译文：{out5!r}")
    out6, _, _, _ = pipeline._translate_units(["   ", ""], StubRoute(), None, cfg)
    check(out6 == "", f"全是空白也空着：{out6!r}")
    print()

    print("=" * 78)
    print("六、缓存有上限（长时间盯着滚动流不能无限涨）")
    print("=" * 78)
    route7 = StubRoute()
    for i in range(4200):
        pipeline._translate_units([f"строка {i}"], route7, None, cfg)
    check(len(pipeline._unit_cache) <= 4000,
          f"缓存被压到 {len(pipeline._unit_cache)} 条")
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
