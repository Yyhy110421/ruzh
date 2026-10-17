"""Does re-splitting recover joined Russian words, without breaking correct ones?

Two things have to hold at once. The known failures must be repaired, and text the
recogniser already got right must come back untouched - a splitter that "fixes" correct
words would trade one failure mode for a worse one.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "wordjoin.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.wordjoin import load_words, restore_spaces, split_joined  # noqa: E402

#: Real joined output from the recogniser -> what it should become.
SHOULD_FIX = [
    ("Спецодеждаиобувьдля сотрудников склада.",
     "Спецодежда и обувь для сотрудников склада."),
    ("Подтвердите дату поставкидо пятницы.",
     "Подтвердите дату поставки до пятницы."),
    ("Цена указана за одну тонну без доставки.", None),      # already correct
    ("Срок поставки — 30 дней с момента оплаты.", None),      # has digits, correct
]

#: Correct Russian that must survive unchanged. Over-splitting here is the real risk.
MUST_NOT_CHANGE = [
    "Спецодежда и обувь для сотрудников склада.",
    "Подтвердите дату поставки до пятницы.",
    "Цена указана за одну тонну без доставки.",
    "Срок поставки 30 дней с момента оплаты.",
    "Встреча перенесена на три часа дня завтра.",
    "Здравствуйте! Подтверждаем получение вашего запроса.",
    "Мы работаем с понедельника по пятницу.",
    "Общая стоимость составляет 450 долларов США.",
]


def main() -> int:
    started = time.perf_counter()
    words = load_words()
    elapsed = time.perf_counter() - started
    print(f"词表加载：{len(words)} 个词，{elapsed:.2f} 秒")
    if not words:
        print("词表为空 —— 后面的测试没有意义")
        return 1
    print()

    failures = 0

    print("=" * 78)
    print("一、应该被修复的粘连")
    print("=" * 78)
    for source, expected in SHOULD_FIX:
        got = restore_spaces(source, words)
        if expected is None:
            ok = got == source
            print(f"  [{'OK  ' if ok else 'FAIL'}] 应当保持不变：{source}")
            if not ok:
                print(f"         变成了：{got}")
        else:
            ok = got == expected
            print(f"  [{'OK  ' if ok else 'FAIL'}] {source}")
            print(f"         期望：{expected}")
            print(f"         实际：{got}")
        if not ok:
            failures += 1
    print()

    print("=" * 78)
    print("二、本来就正确的句子，必须一字不改")
    print("=" * 78)
    for text in MUST_NOT_CHANGE:
        got = restore_spaces(text, words)
        ok = got == text
        if not ok:
            failures += 1
            print(f"  [FAIL] {text}")
            print(f"         被改成了：{got}")
        else:
            print(f"  [OK  ] {text}")
    print()

    print("=" * 78)
    print("三、单词级检查（容易误切的情况）")
    print("=" * 78)
    SINGLES = [
        ("склада", "склада"),              # real word, keep
        ("Спецодежда", "Спецодежда"),      # real word, keep
        ("иобувь", "и обувь"),             # joined, split
        ("длясотрудников", "для сотрудников"),
        ("ценауказана", "цена указана"),
        ("доставки", "доставки"),
        ("информация", "информация"),
        ("предоплата", "предоплата"),
        ("срокиоплаты", "сроки оплаты"),
    ]
    for token, expected in SINGLES:
        got = split_joined(token, words)
        ok = got == expected
        if not ok:
            failures += 1
        print(f"  [{'OK  ' if ok else 'FAIL'}] {token:<18} -> {got:<24}"
              f"（期望 {expected}）")

    print()
    print("=" * 78)
    print(f"失败 {failures} 项")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
