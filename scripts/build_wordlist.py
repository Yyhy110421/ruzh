"""Build the compact Russian word list that ships with the app.

The Cyrillic recogniser drops spaces between short words, so joined output has to be
re-split, and that needs a vocabulary with frequencies. ``wordfreq`` (54 MB) provides
one at build time; only the extracted list is shipped, so the runtime dependency stays
at zero.

    pip install wordfreq
    python scripts/build_wordlist.py            # writes app/data/ru_words.txt
    python scripts/build_wordlist.py --check    # report coverage only
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

TARGET = ROOT / "app" / "data" / "ru_words.txt"

#: Words from the real failure cases, used to pick how large the list has to be.
SAMPLE_WORDS = [
    "спецодежда", "обувь", "сотрудников", "склада", "цена", "указана", "тонну",
    "доставки", "поставки", "пятницы", "подтвердите", "перенесена", "встреча",
    "оплаты", "срок", "долларов", "составляет", "запроса", "получение", "здравствуйте",
]


def coverage(words: list[str], size: int) -> tuple[int, list[str]]:
    known = set(words[:size])
    missing = [w for w in SAMPLE_WORDS if w not in known]
    return len(SAMPLE_WORDS) - len(missing), missing


def build(size: int) -> int:
    from wordfreq import zipf_frequency

    from wordfreq import top_n_list

    words = top_n_list("ru", size)
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for word in words:
        if len(word) < 1:
            continue
        score = zipf_frequency(word, "ru")
        if score <= 0:
            continue
        lines.append(f"{word}\t{score:.2f}")
    TARGET.write_text("# 俄语常用词与词频（由 scripts/build_wordlist.py 从 wordfreq 导出）\n"
                      + "\n".join(lines) + "\n", encoding="utf-8")
    kb = TARGET.stat().st_size / 1024
    print(f"写入 {TARGET.relative_to(ROOT)}：{len(lines)} 个词，{kb:.0f} KB")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成俄语词表")
    parser.add_argument("--check", action="store_true", help="只报告覆盖率，不写文件")
    parser.add_argument("--size", type=int, default=60000, help="导出多少个词")
    args = parser.parse_args()

    from wordfreq import top_n_list

    sizes = [10000, 20000, 40000, 60000, 100000, 200000] if args.check else [args.size]
    pool = top_n_list("ru", max(sizes))
    print("样本词覆盖率：")
    for size in sizes:
        hit, missing = coverage(pool, size)
        note = "" if not missing else f"   缺：{'、'.join(missing[:6])}"
        print(f"  前 {size:>7} 词：{hit}/{len(SAMPLE_WORDS)}{note}")

    if args.check:
        return 0
    return build(args.size)


if __name__ == "__main__":
    sys.exit(main())
