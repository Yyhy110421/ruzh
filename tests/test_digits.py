"""Numbers the Cyrillic recogniser spells as letters.

A trade document is made of numbers - quantities, prices, dates, sizes - and the Cyrillic
model emits letters for them. Measured on a clean render of "Срок поставки 30 календарных
дней с момента оплаты." it recognised "зо календарных дней", and the translation came back
as "付款之日起历日的交付期": the 30 was simply gone, and the sentence with it.

The repair cannot be a blanket letter-to-digit swap, because the same pairs read both ways:
"до" (until) and "об" (about) are ordinary words spelled entirely in lookalike letters. What
separates them is the word-frequency list, and this test pins down both halves of that -
what gets repaired, what must not, and the score gap the decision rests on.

Run: python tests/test_digits.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "digits.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.ocr_engine import _WORD_SCORE_FLOOR, repair_digits  # noqa: E402
from app.wordjoin import load_words  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


#: (as the recogniser emitted it, what it must become)
REPAIRED = [
    ("Срок поставки зо календарных дней", "Срок поставки 30 календарных дней"),
    ("цена 1 ооо рублей", "цена 1 000 рублей"),
    ("отгрузка чо ящиков", "отгрузка 40 ящиков"),
    ("Коэффициент б5", "Коэффициент 65"),
    ("партия зоо тонн", "партия 300 тонн"),
]

#: Nothing here may change. The first two are the reason the rule needs the word list at
#: all; the rest are ordinary text that must survive untouched.
UNTOUCHED = [
    "до 30 дней",
    "об этом",
    "ООО Ромашка",
    "ЗАО Петров",
    "оплата в рублях",
    "не позднее 15 числа",
    "на складе в Москве",
    "доставка включена",
    "Цена 1 200 руб. за тонну",
    "Срок 30 календарных дней",
]


def main() -> int:
    print("=" * 78)
    print("一、该修的修了")
    print("=" * 78)
    for src, want in REPAIRED:
        got = repair_digits(src)
        check(got == want, f"{src!r}", f"-> {got!r}，期望 {want!r}" if got != want else "")
    print()

    print("=" * 78)
    print("二、不该碰的一个都没碰")
    print("=" * 78)
    for text in UNTOUCHED:
        got = repair_digits(text)
        check(got == text, f"{text!r}", f"被改成了 {got!r}" if got != text else "")
    print()

    print("=" * 78)
    print("三、判据本身：词表分数真的分得开吗")
    print("=" * 78)
    # The rule rests on real words scoring higher than corpus debris. If the list is ever
    # replaced, this fails loudly instead of silently repairing "до" into "90".
    words = load_words()
    check(bool(words), f"词表能用（{len(words)} 条）")

    real = ["до", "об", "он", "мы", "вы", "их", "из", "за", "на", "по", "со", "то",
            "же", "ли", "бы", "не", "ни", "но", "да", "ее"]
    debris = ["зо", "чо", "бо", "зб", "оо", "дд", "зз", "бб"]
    low_real = min((words.get(w, 99.0) for w in real), default=99.0)
    high_debris = max((words.get(w, 0.0) for w in debris), default=0.0)
    print(f"        常见词里最低的分数：{low_real:.2f}")
    print(f"        垃圾串里最高的分数：  {high_debris:.2f}")
    print(f"        阈值：               {_WORD_SCORE_FLOOR:.2f}")
    check(high_debris < _WORD_SCORE_FLOOR < low_real,
          f"垃圾全在阈值下、真词全在阈值上（{high_debris:.2f} < "
          f"{_WORD_SCORE_FLOOR:.2f} < {low_real:.2f}）")

    # ООО sits on the wrong side of the threshold and is saved only by being capitalised.
    ooo = words.get("ооо", 0.0)
    print(f"        ООО 的分数：{ooo:.2f}（在阈值下，靠全大写保护）")
    check(repair_digits("ООО Ромашка") == "ООО Ромашка", "ООО 没被改成 000")
    check(repair_digits("ооо рублей") == "000 рублей",
          "小写的 ооо 会被修（它不可能是公司形式）")
    print()

    print("=" * 78)
    print("四、识别流程里真的调了它")
    print("=" * 78)
    source = (ROOT / "app" / "ocr_engine.py").read_text(encoding="utf-8")
    in_cyrillic = "repair_digits(text)" in source
    check(in_cyrillic, "repair_digits 在识别流程里被调用")
    # Order matters: restore_spaces skips tokens containing digits, so the digits have to
    # be real ones before it runs.
    ix_repair = source.find("text = repair_digits(text)")
    ix_spaces = source.find("text = restore_spaces(text)")
    check(0 < ix_repair < ix_spaces, "在 restore_spaces 之前调用")
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
