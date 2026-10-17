"""Repairing the words the Cyrillic recogniser mangles.

Measured over twenty rendered trade sentences, the recogniser's letter-level errors were
a capital where a lowercase belongs (harmless to a translator), a dropped or L-shaped Ц,
a colon read as "л", "щ" read as "ц", "ь" read as "б", a doubled letter, and a word-initial
capital swallowed whole. The harmless kind is left alone; the harmful kind is repaired by
looking the token up in the Russian word list.

The rule is deliberately narrow and this test pins down both halves of it: what gets
fixed, and - more importantly - what must not be. The ambiguity guard is the part that
matters most, and there is a measurement behind it: "ена" completes to twenty different
words, and the most frequent of them is "жена" (wife), not "цена" (price). Guessing by
frequency would turn a price into a wife. Refusing to guess is the correct behaviour.

Run: python tests/test_ocr_repair.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "ocr_repair.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.ocr_engine import repair_words  # noqa: E402
from app.wordjoin import load_words  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


#: (as recognised, as it should read) - every one of these came out of the measurement.
REPAIRED = [
    ("Условия оплатыл предоплата", "Условия оплаты предоплата"),   # ":" read as "л"
    ("Обцая сумма", "Общая сумма"),                                 # щ read as ц
    ("осуцествляется", "осуществляется"),                           # щ read as ц
    ("Стоимостб доставки", "Стоимость доставки"),                   # ь read as б
    ("соответтствует", "соответствует"),                            # doubled letter
    ("арантия на продукцию", "гарантия на продукцию"),              # swallowed Г
    ("ришлите пожалуйста", "пришлите пожалуйста"),                  # swallowed П
    ("ентральный офис", "центральный офис"),                        # swallowed Ц
]

#: Must come back untouched.
UNTOUCHED = [
    "Цена за единицу 1200 рублей",       # ordinary text
    "Общая сумма с НДС",                 # ordinary text
    "до 30 дней",                        # short words are not examined
    "ООО Ромашка",                       # a name the list does not contain
    "плата производится",                # a real word, even though a letter was lost
    "требованиям качества",              # a real word
]

#: The guard that keeps this safe. Each of these is unrecognised but repairs to more than
#: one word, so the correct answer is to leave it exactly as recognised.
AMBIGUOUS = [
    "ена",      # жена? цена? лена? сена? - twenty ways, and the commonest is "жена"
    "лены",     # члены? елены? алены? клены?
]


def main() -> int:
    words = load_words()
    check(bool(words), f"词表能用（{len(words)} 条）")
    print()

    print("=" * 78)
    print("一、该修的修了（全部来自实测）")
    print("=" * 78)
    for src, want in REPAIRED:
        got = repair_words(src, words)
        check(got == want, f"{src!r}", f"-> {got!r}，期望 {want!r}" if got != want else "")
    print()

    print("=" * 78)
    print("二、不该碰的一个都没碰")
    print("=" * 78)
    for text in UNTOUCHED:
        got = repair_words(text, words)
        check(got == text, f"{text!r}", f"被改成了 {got!r}" if got != text else "")
    print()

    print("=" * 78)
    print("三、有多种补法的一律不猜（这条最重要）")
    print("=" * 78)
    from app.ocr_engine import _HEAD_LETTERS

    for token in AMBIGUOUS:
        ways = [c + token for c in _HEAD_LETTERS if (c + token) in words]
        got = repair_words(token, words)
        print(f"        {token!r} 有 {len(ways)} 种补法成词，"
              f"最常见的是 {max(ways, key=lambda w: words[w])!r}" if ways else "")
        check(got == token, f"{token!r} 原样保留",
              f"被猜成了 {got!r}" if got != token else "")

    # The specific trap: guessing by frequency turns a price into a wife.
    wife = words.get("жена", 0.0)
    price = words.get("цена", 0.0)
    print(f"        жена={wife:.2f}  цена={price:.2f}")
    check(wife > price,
          "数据仍然成立：按词频猜会猜错（所以不能按词频猜）")
    print()

    print("=" * 78)
    print("四、识别流程里真的调了它")
    print("=" * 78)
    source = (ROOT / "app" / "ocr_engine.py").read_text(encoding="utf-8")
    check("text = repair_words(text)" in source, "repair_words 在识别流程里被调用")
    ix_words = source.find("text = repair_words(text)")
    ix_spaces = source.find("text = restore_spaces(text)")
    check(0 < ix_spaces < ix_words, "在 restore_spaces 之后调用（先分词再查词）")
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
