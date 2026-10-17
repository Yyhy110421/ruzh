# -*- coding: utf-8 -*-
"""How well does the shipped pipeline read Russian trade text?

Twenty real sentences, rendered, run through the recogniser exactly as the app runs it,
then compared character by character. Reports two numbers because they mean different
things:

* **exact** - identical to the original, case included. Depressing, and mostly irrelevant.
* **case-folded** - identical once case is ignored. This is the one that matters: the
  measurement showed most "errors" are a capital where a lowercase belongs ("в" read as
  "В"), and a translation model does not care. What does care is a word the recogniser
  turned into a different word.

The confusion counts at the end are what the repair rules in app/ocr_engine.py were built
from, so this also shows whether they are still the right rules.

    python tests/eval_ocr_quality.py
"""
import collections
import difflib
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "ocr_quality.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

SENTENCES = [
    "Цена за единицу 1200 рублей.",
    "Общая сумма с НДС составляет 600 000 рублей.",
    "Условия оплаты: предоплата 30 процентов.",
    "Срок изготовления 45 рабочих дней.",
    "Центральный офис находится в Москве.",
    "Отгрузка со склада в Челябинске.",
    "Мы готовы поставить товар в течение месяца.",
    "Просим подтвердить заказ до конца недели.",
    "Стоимость доставки включена в цену.",
    "Минимальная партия 500 штук.",
    "Оплата производится банковским переводом.",
    "Гарантия на продукцию 12 месяцев.",
    "Направляем вам коммерческое предложение.",
    "Цена указана без учёта доставки.",
    "Товар соответствует требованиям качества.",
    "Пришлите, пожалуйста, счёт на оплату.",
    "Мы работаем с юридическими лицами.",
    "Доставка осуществляется транспортной компанией.",
    "Цены действительны до 31 декабря.",
    "Количество товара на складе ограничено.",
]


def main() -> int:
    from PIL import Image, ImageDraw, ImageFont
    import numpy as np

    from app.config import AppConfig
    from app.ocr_engine import OcrEngine, script_for

    cfg = AppConfig()
    cfg.source_lang = "ru"

    font = None
    for name in ("arial.ttf", "segoeui.ttf", "tahoma.ttf"):
        try:
            font = ImageFont.truetype(name, 32)
            break
        except Exception:  # noqa: BLE001
            continue
    if font is None:
        print("没有可用的西里尔字体，跳过")
        return 0

    engine = OcrEngine(cfg.ocr, models_root=cfg.resolved_models_root(),
                       script=script_for("ru"))
    engine.load()

    image = Image.new("RGB", (1700, 70 * len(SENTENCES) + 20), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(SENTENCES):
        draw.text((16, 14 + index * 70), line, fill="black", font=font)

    started = time.perf_counter()
    result = engine.recognize(np.array(image))
    elapsed = time.perf_counter() - started
    got = [line.text for line in result.lines]

    def fold(text: str) -> str:
        return text.lower().replace("ё", "е")

    exact = 0
    folded = 0
    fragments = 0
    subs: collections.Counter = collections.Counter()
    dels: collections.Counter = collections.Counter()

    for index, want in enumerate(SENTENCES):
        have = got[index] if index < len(got) else ""
        if have == want:
            exact += 1
        if fold(have) == fold(want):
            folded += 1
            continue
        matcher = difflib.SequenceMatcher(None, want, have)
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue
            fragments += 1
            if tag == "replace":
                for a, b in zip(want[i1:i2], have[j1:j2]):
                    if a.lower() != b.lower():
                        subs[(a, b)] += 1
            elif tag == "delete":
                for a in want[i1:i2]:
                    dels[a] += 1

    print("=" * 78)
    print(f"识别 {len(got)} 行，用时 {elapsed:.1f} 秒")
    print("=" * 78)
    print(f"  一模一样（含大小写）：{exact}/{len(SENTENCES)}")
    print(f"  忽略大小写后一致：    {folded}/{len(SENTENCES)}"
          f"   ← 这个才是翻译真正在意的")
    print(f"  不同片段：            {fragments}")
    print()
    print("=== 认错的字（排除大小写）===")
    if subs:
        for (a, b), n in subs.most_common(15):
            print(f"  {a!r} 被认成 {b!r}   {n} 次")
    else:
        print("  没有")
    print()
    print("=== 被丢掉的字 ===")
    if dels:
        for a, n in dels.most_common(12):
            print(f"  {a!r} 丢了 {n} 次")
    else:
        print("  没有")
    print()
    print("=== 原句 vs 识别（只列不一样的）===")
    for index, want in enumerate(SENTENCES):
        have = got[index] if index < len(got) else ""
        if fold(have) != fold(want):
            print(f"  原: {want}")
            print(f"  认: {have}")
            print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
