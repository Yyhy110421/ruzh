"""Numbers have to survive translation.

Measured on ten trade sentences through the offline route (Russian to English to Chinese),
the English hop mangled the numbers in six of them:

    600000 рублей   ->  "600,0 roubles"
    от 1000 штук    ->  "from the 1,0 ..."
    по 20 штук      ->  "of 20 grand"
    500 штук        ->  the whole clause dropped

Rewriting the separators did not help - "600000" comes back as "600,0" just the same - so
the digits are taken out of the sentence before translation and put back afterwards.

Two things are pinned here: the mechanics, and the fact that the offline engine really does
keep the numbers now. The second is the one that matters, and it is the reason this file
loads a model.

Run: python tests/test_numbers.py
"""
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "numbers.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.numbers import mask, normalize_russian, unmask  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


#: Sentences that must come through with the number intact, and the number to look for.
KEEP = [
    ("Цена за единицу 1200 рублей без НДС.", "1200"),
    ("Общая сумма с НДС составляет 600 000 рублей.", "600000"),
    ("Минимальная партия 500 штук, меньше не отгружаем.", "500"),
    ("Упаковка в картонные коробки по 20 штук.", "20"),
    ("При заказе от 1000 штук скидка 5 процентов.", "1000"),
    ("Срок изготовления 45 рабочих дней с момента оплаты.", "45"),
    ("Гарантия на продукцию 12 месяцев со дня отгрузки.", "12"),
    ("Отгрузка со склада в Челябинске в течение 5 дней.", "5"),
]


def main() -> int:
    print("=" * 78)
    print("一、俄语数字格式的归一化")
    print("=" * 78)
    for src, want in [
        ("600 000 рублей", "600000 рублей"),
        ("1 200 000 руб", "1200000 руб"),
        ("Цена 1 200 руб.", "Цена 1200 руб."),
        ("1,5 тонны", "1.5 тонны"),
        # Must not touch: two digits before the space is not a thousands group.
        ("30 дней", "30 дней"),
        ("1000 штук", "1000 штук"),
    ]:
        got = normalize_russian(src)
        check(got == want, f"{src!r} -> {got!r}", f"期望 {want!r}" if got != want else "")
    print()

    print("=" * 78)
    print("二、掩码往返")
    print("=" * 78)
    for text in [
        "Общая сумма 600 000 руб. и 30% и 1,5 тонны",
        "от 1000 штук скидка 5 процентов",
        "Срок 45 рабочих дней",
        "никаких цифр тут нет",
    ]:
        masked, mapping = mask(text)
        back, lost = unmask(masked, mapping)
        check(back == text and not lost, f"往返一致：{text[:40]!r}",
              f"\n         掩: {masked}\n         还: {back}\n         丢: {lost}"
              if back != text or lost else "")
    print()

    print("=" * 78)
    print("三、同一个数字只占一个号")
    print("=" * 78)
    masked, mapping = mask("500 штук и снова 500 штук")
    check(len(mapping) == 1, f"出现两次只记一个（{len(mapping)} 个）", masked)
    print()

    print("=" * 78)
    print("四、引擎弄丢了数字，必须报出来")
    print("=" * 78)
    masked, mapping = mask("Цены действительны до 31 декабря.")
    eaten = masked.replace("[N1]", "")
    back, lost = unmask(eaten, mapping)
    check(lost == ["[N1]"], f"丢的那个被报出来：{lost}")
    print()

    print("=" * 78)
    print("五、真跑一遍离线引擎（这条最要紧）")
    print("=" * 78)
    from app.config import AppConfig
    from app.text_tool import translate_text

    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = "opus"

    kept = 0
    started = time.perf_counter()
    for sentence, number in KEEP:
        try:
            result = translate_text(cfg, cfg.resolved_models_root(), sentence, mode="line")
            out = result.translation.strip()
            lost = list(result.numbers_unrestored)
        except Exception as exc:  # noqa: BLE001
            out, lost = f"<{exc}>", []
        ok = number in out
        if ok:
            kept += 1
        check(ok, f"{number} 还在",
              "" if ok else f"\n         原文: {sentence}\n         译文: {out}\n         丢失: {lost}")
    print(f"        保住 {kept} / {len(KEEP)}，用时 {time.perf_counter()-started:.0f} 秒")
    check(kept >= len(KEEP) - 1, "数字基本全保住（允许一条被引擎吞掉，但要报出来）")
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
