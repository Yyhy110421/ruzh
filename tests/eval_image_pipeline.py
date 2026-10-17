# -*- coding: utf-8 -*-
"""Does translating a picture of a contract actually work end to end?

Everything else about the picture path is tested with stubs; this one loads the real
recogniser and the real translator and runs a rendered contract through both. It is the
only place that answers the two questions a customer would ask: did it read the page, and
did the numbers survive.

Run: python tests/eval_image_pipeline.py
"""
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "image_pipeline.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

#: A contract as it arrives: a heading, two sides, three clauses. Wrapped where a real
#: one wraps, so the paragraph rebuild has something to rebuild.
PAGE = [
    "ДОГОВОР ПОСТАВКИ N 2024/17",
    "",
    "Поставщик: ООО Ромашка, г. Москва",
    "Покупатель: ООО Лютик, г. Новосибирск",
    "",
    "1. Предмет договора",
    "Поставщик обязуется поставить товар, а Покупатель",
    "обязуется принять и оплатить его на условиях",
    "настоящего договора.",
    "",
    "2. Цена и порядок расчётов",
    "Общая сумма договора составляет 600 000 рублей",
    "с учётом НДС. Предоплата 30 процентов в течение",
    "5 банковских дней.",
    "",
    "3. Сроки поставки",
    "Срок поставки 45 календарных дней с момента оплаты.",
    "",
    "4. Адрес отгрузки",
    "Отгрузка со склада в Челябинске.",
]

failures = 0


def check(ok, label, extra=""):
    global failures
    if not ok:
        failures += 1
    print("  [" + ("OK  " if ok else "FAIL") + "] " + label
          + ("  " + extra if extra else ""))


def main() -> int:
    from PIL import Image, ImageDraw, ImageFont

    from app.config import AppConfig
    from app.image_tool import translate_image

    cfg = AppConfig()
    cfg.source_lang = "ru"
    cfg.target_lang = "zh"

    font = None
    for name in ("arial.ttf", "segoeui.ttf", "tahoma.ttf"):
        try:
            font = ImageFont.truetype(name, 28)
            break
        except Exception:  # noqa: BLE001
            continue
    if font is None:
        print("没有可用的西里尔字体，跳过")
        return 0

    image = Image.new("RGB", (1100, 46 * len(PAGE) + 40), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(PAGE):
        if line:
            draw.text((30, 20 + index * 46), line, fill="black", font=font)

    shot = ROOT / "tests" / "image_pipeline.png"
    image.save(shot)
    print("渲染好的合同图：" + str(shot))
    print()

    started = time.perf_counter()
    result = translate_image(cfg, cfg.resolved_models_root(), shot)
    elapsed = time.perf_counter() - started

    print("=" * 78)
    print("识别出来的原文")
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
    check(not result.empty, "认出了文字")
    check(result.paragraphs >= 4,
          "段落数合理（" + str(result.paragraphs) + " 段）")
    check(result.failed == 0,
          "没有翻译失败的句子（" + str(result.failed) + "）")

    source = result.original_text
    target = result.translated_text
    # The whole reason app/numbers.py exists: a quotation whose amounts are wrong is
    # worse than no quotation, and a reader cannot spot a missing quantity.
    for number in ("600 000", "30", "45", "5"):
        check(number in source, "原文里有 " + number)
    for number in ("600000", "600 000"):
        if number in target:
            check(True, "译文保住了总额 " + number)
            break
    else:
        check(False, "译文保住了总额", "译文里找不到 600000：" + target[:200])
    check("30" in target, "译文保住了 30")
    check("45" in target, "译文保住了 45")

    # The glossary is what makes this a product rather than a model: a city the offline
    # engine renders as 新闻社 or 人 is a place the customer has to recognise.
    for city, chinese in (("Москв", "莫斯科"), ("Новосибирск", "新西伯利亚"),
                          ("Челябинск", "车里雅宾斯克")):
        if city in source:
            check(chinese in target, "城市 " + city + " 译成 " + chinese)

    print()
    print("  识别 " + f"{result.ocr_seconds:.1f}" + " 秒，"
          + "总共 " + f"{elapsed:.1f}" + " 秒")
    print("  脱敏 " + str(result.redacted) + " 处，"
          + "数字保护 " + str(result.numbers_protected) + " 个")
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
