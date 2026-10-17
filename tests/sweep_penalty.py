"""Pick the word-insertion penalty empirically instead of guessing at it.

The penalty decides how eagerly joined text is cut apart. Too low and Спецодеждаиобувьдля
becomes eleven single letters (в, и, с, к are all real Russian words); too high and
genuine two-word joins are never repaired. Both directions are failures, so sweep the
value over a labelled set and take the one that satisfies every case.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "penalty_sweep.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.wordjoin import load_words, split_joined  # noqa: E402

#: (token, expected). Every one of these must hold.
CASES = [
    # joined by the recogniser -> must be repaired
    ("Спецодеждаиобувьдля", "Спецодежда и обувь для"),
    ("иобувь", "и обувь"),
    ("длясотрудников", "для сотрудников"),
    ("ценауказана", "цена указана"),
    ("срокиоплаты", "сроки оплаты"),
    ("поставкидо", "поставки до"),
    ("получениевашегозапроса", "получение вашего запроса"),
    ("Подтвердитедату", "Подтвердите дату"),
    # already-correct words -> must survive untouched
    ("склада", "склада"),
    ("Спецодежда", "Спецодежда"),
    ("доставки", "доставки"),
    ("информация", "информация"),
    ("предоплата", "предоплата"),
    ("стоимость", "стоимость"),
    ("сотрудников", "сотрудников"),
    ("Уважаемый", "Уважаемый"),
    # names and rare words: better left alone than cut into pieces
    ("Новосибирск", "Новосибирск"),
]


def main() -> int:
    words = load_words()
    print(f"词表 {len(words)} 个词")
    print()
    print(f"{'惩罚值':>8} {'通过':>8}  失败样例")
    print("-" * 74)

    best_penalty = None
    best_score = -1
    for penalty in [4.0, 5.0, 5.5, 6.0, 6.5, 7.0, 7.5, 8.0, 9.0, 10.0, 12.0]:
        passed = 0
        failures: list[str] = []
        for token, expected in CASES:
            got = split_joined(token, words, penalty=penalty)
            if got == expected:
                passed += 1
            else:
                failures.append(f"{token}->{got}")
        flag = "  <== 全过" if passed == len(CASES) else ""
        print(f"{penalty:>8.1f} {passed:>4}/{len(CASES):<3}"
              f"  {'; '.join(failures[:3])}{flag}")
        if passed > best_score:
            best_score = passed
            best_penalty = penalty

    print()
    print(f"最佳惩罚值：{best_penalty}（{best_score}/{len(CASES)}）")
    if best_score == len(CASES):
        print("\n全部用例通过，各用例结果：")
        for token, expected in CASES:
            got = split_joined(token, words, penalty=best_penalty)
            mark = "OK  " if got == expected else "FAIL"
            print(f"  [{mark}] {token:<26} -> {got}")
    return 0 if best_score == len(CASES) else 1


if __name__ == "__main__":
    sys.exit(main())
