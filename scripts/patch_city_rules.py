# -*- coding: utf-8 -*-
"""Apply the city Latin-transliteration catch-all to the shipped glossary.

build_trade_glossary.py now does this on every run, but a rebuild means measuring 125
terms against two engines again, and the measured mistakes already in the file are still
good. This applies the same helper to what is already there, so the file catches up
without the six-minute measure.

Idempotent: running it twice changes nothing the second time.

    python scripts/patch_city_rules.py [--dry-run]
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.build_trade_glossary import (  # noqa: E402
    CITIES, OUT, add_latin_catch_all, city_rule, latin_alternation)

#: A few, so the output says what kind of pattern was added rather than only a number.
SHOW = ("Новосибирск", "Челябинск", "Москва")


def main() -> int:
    dry_run = "--dry-run" in sys.argv
    payload = json.loads(OUT.read_text(encoding="utf-8"))
    rules = payload["rules"]
    before = len(rules)

    changed = 0
    for russian, chinese, _back in CITIES:
        rule = city_rule(rules, russian, chinese)
        if add_latin_catch_all(rule, russian):
            changed += 1

    print(f"城市 {len(CITIES)} 个，改了 {changed} 条规则；"
          f"规则总数 {before} → {len(rules)}")
    print()
    for russian, chinese, _back in CITIES:
        if russian not in SHOW:
            continue
        rule = city_rule(rules, russian, chinese)
        print(f"{russian} → {chinese}")
        print(f"  模式: {rule['mode']}")
        print(f"  匹配: {rule['src'][:160]}")
        print()

    # Prove the patterns actually match what the engines produced on the rendered
    # contract, rather than trusting the shape of the string.
    import re

    samples = [("Новосибирск", "Newosibirsk 1"), ("Новосибирск", "Novosibirsk"),
               ("Челябинск", "Chelabinski"), ("Челябинск", "Chelyabinsk"),
               ("Москва", " Moscow ")]
    print("抽样验证：")
    for russian, text in samples:
        hit = re.search(latin_alternation(russian), text)
        print(f"  {'OK  ' if hit else 'FAIL'} {russian:<14} 能接住 {text!r}")
    print()

    if dry_run:
        print("--dry-run，没写文件")
        return 0

    payload["rules"] = rules
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {OUT.relative_to(ROOT)}（{OUT.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
