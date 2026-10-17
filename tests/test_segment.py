"""Does rebuilding paragraphs help, and does it stay out of the way when it should?

Two things have to hold at once. Hard-wrapped prose must be put back together, because a
half sentence cannot be translated; and text that was already line-structured - a subtitle,
a list, a signature block, anything where the line break is meaningful - must come back
untouched. Merging two real paragraphs is the failure that is hard to notice, so most of
what follows is about *not* merging.
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "segment.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.segment import ends_sentence, rebuild_paragraphs, split_sentences  # noqa: E402

failures = 0


def check(condition: bool, label: str, extra: str = "") -> None:
    global failures
    if condition:
        print(f"  [OK  ] {label}")
    else:
        failures += 1
        print(f"  [FAIL] {label}")
        if extra:
            print(f"         {extra}")


def paragraphs(text: str) -> list[str]:
    return [b.text for b in rebuild_paragraphs(text) if b.kind == "para"]


#: Real-shaped trade text. Every pair is (as it arrives, what it must become).
REJOIN = [
    (
        "Срок поставки — 30 календарных дней с момента\n"
        "поступления оплаты на наш расчётный счёт.\n"
        "Оплата производится в рублях.",
        [
            "Срок поставки — 30 календарных дней с момента поступления оплаты "
            "на наш расчётный счёт.",
            "Оплата производится в рублях.",
        ],
    ),
    (
        "Уважаемые партнёры!\n"
        "\n"
        "Направляем вам коммерческое предложение\n"
        "на поставку спецодежды.\n"
        "\n"
        "С уважением,\n"
        "отдел продаж",
        [
            "Уважаемые партнёры!",
            "Направляем вам коммерческое предложение на поставку спецодежды.",
            "С уважением, отдел продаж",
        ],
    ),
    (
        "本合同自双方签字之日起生效，\n"
        "有效期为一年。",
        ["本合同自双方签字之日起生效，有效期为一年。"],
    ),
    (
        "Payment is\n"
        "trans-\n"
        "ferred by wire.",
        ["Payment is transferred by wire."],
    ),
    # The value of a field, wrapped. This one has to join: a rule that keeps every field
    # separate must not also keep a field's own value dangling on its own line.
    (
        "Наименование: Костюм рабочий мужской\n"
        "из хлопка, размер 48",
        ["Наименование: Костюм рабочий мужской из хлопка, размер 48"],
    ),
]

#: Lines that must stay separate. The line break carries meaning here.
MUST_NOT_MERGE = [
    (
        "Условия:\n1. Оплата в рублях.\n2. Доставка за счёт покупателя.",
        ["Условия:", "1. Оплата в рублях.", "2. Доставка за счёт покупателя."],
    ),
    (
        "第一行意思独立。\n第二行也是独立的一句。",
        ["第一行意思独立。", "第二行也是独立的一句。"],
    ),
    (
        "ТОВАРИЩ\n\nОПЛАТА",
        ["ТОВАРИЩ", "ОПЛАТА"],
    ),
    # A form. Every line is a complete field and none of them ends with a full stop, so
    # the hard-wrap rule used to swallow each one into the line above: a real Chinese
    # reply came out as one paragraph reading 单价：1200 卢布，不含运费起订量：500 件, and
    # the translation had the labels attached to the wrong values.
    (
        "产品名称：男式工作服\n"
        "单价：1200 卢布，不含运费\n"
        "起订量：500 件\n"
        "报价有效期：30 天\n"
        "如对规格有疑问，请随时联系。",
        ["产品名称：男式工作服", "单价：1200 卢布，不含运费", "起订量：500 件",
         "报价有效期：30 天", "如对规格有疑问，请随时联系。"],
    ),
    (
        "Наименование: Костюм рабочий\n"
        "Цена: 1200 руб.\n"
        "Срок поставки: 30 дней.",
        ["Наименование: Костюм рабочий", "Цена: 1200 руб.", "Срок поставки: 30 дней."],
    ),
    # Multi-word Russian labels. A cap counted in characters, tuned on the Chinese form
    # above, missed every one of these and merged a whole contract into one paragraph.
    (
        "Наименование: Костюм рабочий мужской\n"
        "Цена за единицу: 1200 руб.\n"
        "Минимальная партия: 500 шт.\n"
        "Срок поставки: 30 календарных дней с момента поступления оплаты.",
        ["Наименование: Костюм рабочий мужской", "Цена за единицу: 1200 руб.",
         "Минимальная партия: 500 шт.",
         "Срок поставки: 30 календарных дней с момента поступления оплаты."],
    ),
]

#: A dot that is not a sentence end. Getting this wrong joins two paragraphs.
NOT_SENTENCE_END = ["Цена — 1 200 руб.", "Иванов И.И.", "заказ № 15.", "п. 3"]

#: A dot that is.
IS_SENTENCE_END = ["Оплата в рублях.", "Подтверждаем заказ!", "Срок — 30 дней?",
                   "合同自签字之日起生效。", "Готово…"]

#: (paragraph, the sentences it must split into)
SENTENCES = [
    ("本合同自双方签字之日起生效，有效期为一年。付款方式为电汇。请确认。",
     ["本合同自双方签字之日起生效，有效期为一年。", "付款方式为电汇。", "请确认。"]),
    ("Здравствуйте! Подтверждаем заказ. Срок — 30 дней.",
     ["Здравствуйте!", "Подтверждаем заказ.", "Срок — 30 дней."]),
    ("Сумма 1.5 млн руб. и срок 30 дней.",
     ["Сумма 1.5 млн руб. и срок 30 дней."]),
    ("Стоимость 1 200 руб. Оплата сразу.",
     ["Стоимость 1 200 руб. Оплата сразу."]),
]


def main() -> int:
    global failures
    print("=" * 78)
    print("一、硬折行的段落要合回去")
    print("=" * 78)
    for source, expected in REJOIN:
        got = paragraphs(source)
        ok = got == expected
        if not ok:
            failures += 1
            print("  [FAIL] 输入：")
            for line in source.split("\n"):
                print(f"           | {line}")
            print("         期望：")
            for line in expected:
                print(f"           > {line}")
            print("         实际：")
            for line in got:
                print(f"           > {line}")
        else:
            print(f"  [OK  ] {len(got)} 段：{got[0][:46]}")
    print()

    print("=" * 78)
    print("二、本来就是独立行的，一句都不许合（合错比不合更糟）")
    print("=" * 78)
    for source, expected in MUST_NOT_MERGE:
        got = paragraphs(source)
        ok = got == expected
        check(ok, f"{len(expected)} 行保持独立", "" if ok else f"实际：{got}")
    print()

    print("=" * 78)
    print("三、ends_sentence 的判断")
    print("=" * 78)
    for line in NOT_SENTENCE_END:
        check(not ends_sentence(line), f"不算句末：{line}")
    for line in IS_SENTENCE_END:
        check(ends_sentence(line), f"算句末：{line}")
    print()

    print("=" * 78)
    print("四、段落切成句子（送进引擎的单位）")
    print("=" * 78)
    for paragraph, expected in SENTENCES:
        got = split_sentences(paragraph)
        ok = got == expected
        if not ok:
            failures += 1
            print(f"  [FAIL] {paragraph}")
            print(f"         期望：{expected}")
            print(f"         实际：{got}")
        else:
            print(f"  [OK  ] {len(got)} 句：{paragraph[:44]}")
    print()

    print("=" * 78)
    print("五、空行必须原样保留（段间距离是排版）")
    print("=" * 78)
    blocks = rebuild_paragraphs("Первое.\n\n\nВторое.")
    kinds = [b.kind for b in blocks]
    check(kinds == ["para", "blank", "blank", "para"], f"结构：{kinds}")
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
