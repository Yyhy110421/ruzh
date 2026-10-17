"""The shipped glossary has to keep working when the engine changes.

The rules are derived from what an engine gets wrong, so a file built against one engine
is dead on another: measured, the previous file changed 3 of 20 cells on the offline
engine and 0 of 20 on MyMemory - the glossary was doing nothing at all on the engine a
user is most likely to switch to for quality.

The fix folds every engine's rendering into one rule as an alternation, so this test
guards the structure that makes that true. Proving it end to end needs the network and
lives in tests/eval_online_real.py instead.

Run: python tests/test_glossary_engines.py
"""
import ast
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "glossary_engines.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.glossary import Glossary, trade_glossary_path  # noqa: E402

GENERATOR = ROOT / "scripts" / "build_trade_glossary.py"

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


def measured_engines() -> list[str]:
    """The engines the generator measures, read from its source."""
    tree = ast.parse(GENERATOR.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            target = node.targets[0]
            if getattr(target, "id", "") == "MEASURE_ENGINES":
                return [element.value for element in node.value.elts]
    return []


def main() -> int:
    print("=" * 78)
    print("一、生成器要测多个引擎")
    print("=" * 78)
    engines = measured_engines()
    print(f"        MEASURE_ENGINES = {engines}")
    check(len(engines) >= 2,
          f"至少两个引擎参与测量（实际 {len(engines)}）")
    check("opus" in engines, "包含离线引擎 opus")
    check("mymemory" in engines, "包含免费的联网引擎 mymemory")
    print()

    print("=" * 78)
    print("二、词库文件里看得到多引擎的痕迹")
    print("=" * 78)
    path = trade_glossary_path()
    check(path.is_file(), f"内置词库存在：{path.name}")
    if not path.is_file():
        return 1
    glossary = Glossary.load(path)
    rules = glossary.rules
    print(f"        共 {len(rules)} 条规则")
    check(bool(rules), "有规则")

    merged = [r for r in rules if r.mode == "regex"]
    print(f"        其中正则规则 {len(merged)} 条")
    check(bool(merged), "存在合并了多个错法的正则规则")

    bad = []
    for rule in merged:
        try:
            re.compile(rule.src)
        except re.error as exc:
            bad.append((rule.src, str(exc)))
            continue
        # A regular expression has to earn its place. Two things justify one: an
        # alternation merging what several engines produced, or a city's Latin catch-all
        # (there is only one right answer for a place name, so that rule matches a shape
        # rather than a list of observed mistakes - see latin_alternation).
        if "|" not in rule.src and "(?i:" not in rule.src:
            bad.append((rule.src, "既没合并错法，也不是地名兜底"))
    check(not bad, f"每条正则规则都有理由（{len(bad)} 条不合规）")
    for src, why in bad[:5]:
        print(f"          {src[:60]!r}: {why}")
    print()

    print("=" * 78)
    print("三、合并之后仍然带 when 条件（不能被这次改动弄丢）")
    print("=" * 78)
    missing = [r for r in rules if not r.when]
    print(f"        没有 when 条件的规则：{len(missing)} 条")
    check(not missing, "每条规则都只在原文出现该术语时才动手")
    guarded_regex = [r for r in merged if r.when]
    check(len(guarded_regex) == len(merged), "正则规则同样带条件")
    print()

    print("=" * 78)
    print("四、城市名句子里被写成拉丁字母，也要能接住")
    print("=" * 78)
    # Measured on a rendered contract: Новосибирск came back as "Newosibirsk" and
    # Челябинск as "Chelabinski". Neither is a Chinese misrendering, so the patch built
    # from measuring the bare name never fired and the customer read a transliteration
    # where a city belongs.
    by_when = {}
    for rule in rules:
        if rule.side == "target" and rule.pairs == ["ru->zh"] and rule.when:
            by_when.setdefault(rule.when, []).append(rule)

    from scripts.build_trade_glossary import CITIES, latin_alternation

    without = [russian for russian, _zh, _b in CITIES if russian not in by_when]
    check(not without, f"每个城市都有一条规则（缺 {len(without)} 个）")
    for name in without[:5]:
        print(f"          缺 {name}")

    no_latin = [russian for russian, _zh, _b in CITIES
                if not any("(?i:" in rule.src for rule in by_when.get(russian, []))]
    check(not no_latin, f"每个城市都带拉丁兜底（缺 {len(no_latin)} 个）")
    for name in no_latin[:5]:
        print(f"          缺 {name}")

    # The patterns are only useful if they match what the engines actually emitted.
    observed = [
        ("Новосибирск", "Newosibirsk 1"),
        ("Новосибирск", "Novosibirsk"),
        ("Челябинск", "Chelabinski"),
        ("Челябинск", "Chelyabinsk"),
        ("Москва", " Moscow "),
        ("Екатеринбург", "Yekaterinburg"),
        ("Владивосток", "Vladivostok"),
    ]
    for russian, spelling in observed:
        hit = re.search(latin_alternation(russian), spelling)
        check(bool(hit), f"{russian} 能接住 {spelling!r}")
    print()

    print("=" * 78)
    print("五、示例")
    print("=" * 78)
    for rule in merged[:6]:
        pattern = re.compile(rule.src)
        wrong = "、".join(f"「{one}」" for one in rule.src.split("|")[:3])
        print(f"        原文含 {rule.when!r} 时：{wrong} → 「{rule.dst}」")
        note = getattr(rule, "note", "")
        if note:
            print(f"          来源：{note[:78]}")
        del pattern
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
