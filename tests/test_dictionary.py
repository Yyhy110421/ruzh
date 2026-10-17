"""Does connecting the dictionary actually improve term translation?

The claim being tested is narrow and checkable: for a single trade term the machine
translator has to guess, while a dictionary entry is the answer. So run the same terms
through both paths and count how often each agrees with the term a trade user wants.

The opposite failure matters too - a dictionary must not hijack whole sentences, where
it has no chance of ordering words correctly.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "dict_quality.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import dictlookup  # noqa: E402
from app.config import AppConfig  # noqa: E402
from app.engine import build  # noqa: E402

#: (俄语术语, 期望中文). A subset of the trade concepts, all of which are single terms.
RU_TERMS = [
    ("цена", "价格"), ("скидка", "折扣"), ("предоплата", "预付款"),
    ("аккредитив", "信用证"), ("склад", "仓库"), ("таможня", "海关"),
    ("контейнер", "集装箱"), ("упаковка", "包装"), ("поддон", "托盘"),
    ("количество", "数量"), ("тонна", "吨"), ("договор", "合同"),
    ("образец", "样品"), ("гарантия", "保质期"), ("спецификация", "规格"),
    ("качество", "质量"), ("отгрузка", "发货"), ("заказ", "订单"),
    ("запрос", "询价"), ("предложение", "报价"), ("товар", "货物"),
    ("поставка", "供货"), ("инвойс", "商业发票"), ("накладная", "运单"),
    ("маркировка", "唛头"),
]

#: Whole sentences. The dictionary must stay out of these.
SENTENCES = [
    "Подтверждаем получение вашего запроса.",
    "Цена указана за одну тонну без доставки.",
    "Срок поставки составляет 30 дней.",
]


def main() -> int:
    print("=" * 78)
    print("词典状态")
    print("=" * 78)
    if not dictlookup.available():
        print("  词典不可用 —— 后面的对比没有意义")
        return 1
    info = dictlookup.info()
    print(f"  来源  : {info.get('source')}")
    print(f"  授权  : {info.get('license')}")
    print(f"  词条数: {info.get('pairs'):,}")
    print()

    print("=" * 78)
    print("词典查词样例")
    print("=" * 78)
    for term in ("цена", "предоплата", "контейнер", "маркировка", "упаковка", "海关"):
        for src, tgt in (("ru", "zh"),) if term.isascii() or any(
                "\u0400" <= ch <= "\u04ff" for ch in term) else (("zh", "ru"),):
            hits = dictlookup.lookup(term, src, tgt)
            print(f"  {src}->{tgt}  {term:<14} -> {' | '.join(hits[:4]) or '（未收录）'}")
    print()

    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = "opus"
    route = build(cfg, str(ROOT / "models"))
    route.load()

    print("=" * 78)
    print("术语对比：机器翻译 vs 词典")
    print("=" * 78)
    mt_ok = dict_ok = dict_found = 0
    rows: list[tuple[str, str, str, str, str]] = []
    for russian, expected in RU_TERMS:
        machine = route.translate(russian).strip()
        hits = dictlookup.lookup(russian, "ru", "zh")
        entry = hits[0] if hits else ""
        if hits:
            dict_found += 1
        # Exact match, not substring: the engine happily answers "价格价格" or repeats
        # "海关" hundreds of times, and counting those as correct would flatter it.
        machine_hit = machine == expected
        dict_hit = entry == expected
        mt_ok += machine_hit
        dict_ok += dict_hit
        rows.append((russian, expected, machine[:24], entry,
                     f"{'✓' if machine_hit else '✗'}/{'✓' if dict_hit else '✗'}"))

    for russian, expected, machine, entry, flag in rows:
        print(f"  {russian:<14} 期望 {expected:<8} 机器 {machine:<14} "
              f"词典 {entry or '（未收录）':<14} {flag}")

    total = len(RU_TERMS)
    print()
    print(f"  机器翻译正确 {mt_ok}/{total}")
    print(f"  词典收录     {dict_found}/{total}")
    print(f"  词典正确     {dict_ok}/{total}")
    print()

    print("=" * 78)
    print("整句不得被词典劫持")
    print("=" * 78)
    hijacked = 0
    for sentence in SENTENCES:
        override = dictlookup.term_override(sentence, "ru", "zh")
        ok = override is None
        if not ok:
            hijacked += 1
        print(f"  [{'OK  ' if ok else 'FAIL'}] {sentence}")
        print(f"         词典是否接管：{'是 -> ' + str(override) if not ok else '否，交给翻译引擎'}")

    print()
    print("=" * 78)
    print(f"结论：术语正确率 机器 {mt_ok}/{total} -> 词典 {dict_ok}/{total}"
          f"；整句被劫持 {hijacked} 句")
    print("=" * 78)
    return 0 if hijacked == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
