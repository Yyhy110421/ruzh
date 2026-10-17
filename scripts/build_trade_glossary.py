"""Build the built-in China-Russia trade glossary, measured rather than guessed.

A glossary only helps if its rules match the text they are meant to fix. Guessing what
the machine translator emits is exactly the wrong approach - so this runs the offline
engine over each term in both directions, compares the result with the term a trade
user actually wants, and emits a rule only where they differ. The rules that ship are
therefore known to fire on this engine.

    python scripts/build_trade_glossary.py            # measure and write
    python scripts/build_trade_glossary.py --dry-run   # report only

The output is a normal glossary file, so everything stays editable in the UI and the
user's own rules are applied after the built-in ones and win.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

OUT = ROOT / "app" / "data" / "trade_glossary.json"

#: (russian, preferred Chinese, preferred Russian when writing back).
#: Grouped by what a trade conversation actually turns on. The Russian column is what
#: the user reads in a supplier's message; the Chinese column is what they need it to
#: become, and what they type when replying.
CONCEPTS: list[tuple[str, str, str]] = [
    # -- 报价与价格 -------------------------------------------------------
    ("цена", "价格", "цена"),
    ("стоимость", "总价", "стоимость"),
    ("цена за единицу", "单价", "цена за единицу"),
    ("прайс-лист", "报价单", "прайс-лист"),
    ("оптовая цена", "批发价", "оптовая цена"),
    ("розничная цена", "零售价", "розничная цена"),
    ("скидка", "折扣", "скидка"),
    ("цена без НДС", "不含税价", "цена без НДС"),
    ("цена с НДС", "含税价", "цена с НДС"),
    ("общая сумма", "总金额", "общая сумма"),
    ("минимальная партия", "起订量", "минимальная партия"),
    # -- 付款 -------------------------------------------------------------
    ("предоплата", "预付款", "предоплата"),
    ("полная оплата", "全款", "полная оплата"),
    ("оплата по счёту", "按发票付款", "оплата по счёту"),
    ("банковский перевод", "银行转账", "банковский перевод"),
    ("аккредитив", "信用证", "аккредитив"),
    ("отсрочка платежа", "账期", "отсрочка платежа"),
    ("условия оплаты", "付款条件", "условия оплаты"),
    ("счёт на оплату", "付款通知", "счёт на оплату"),
    ("задолженность", "欠款", "задолженность"),
    # -- 交货与物流 -------------------------------------------------------
    ("срок поставки", "交期", "срок поставки"),
    ("дата поставки", "交货日期", "дата поставки"),
    ("отгрузка", "发货", "отгрузка"),
    ("доставка", "运费", "доставка"),
    ("без учёта доставки", "不含运费", "без учёта доставки"),
    ("включая доставку", "含运费", "включая доставку"),
    ("срок производства", "生产周期", "срок производства"),
    ("партия товара", "一批货", "партия товара"),
    ("контейнер", "集装箱", "контейнер"),
    ("склад", "仓库", "склад"),
    ("таможня", "海关", "таможня"),
    ("таможенная декларация", "报关单", "таможенная декларация"),
    ("груз", "货物", "груз"),
    ("транспортные расходы", "运输费用", "транспортные расходы"),
    ("образец", "样品", "образец"),
    # -- 质量与规格 -------------------------------------------------------
    ("качество", "质量", "качество"),
    ("сертификат качества", "质量证书", "сертификат качества"),
    ("спецификация", "规格", "спецификация"),
    ("стандарт", "标准", "стандарт"),
    ("гарантия", "保质期", "гарантия"),
    ("соответствие", "合格", "соответствие"),
    # -- 包装 -------------------------------------------------------------
    ("упаковка", "包装", "упаковка"),
    ("тара", "外包装", "тара"),
    ("маркировка", "唛头", "маркировка"),
    ("поддон", "托盘", "поддон"),
    ("картонная коробка", "纸箱", "картонная коробка"),
    # -- 数量 -------------------------------------------------------------
    ("количество", "数量", "количество"),
    ("тонна", "吨", "тонна"),
    ("штука", "件", "штука"),
    ("комплект", "套", "комплект"),
    # -- 合同与单据 -------------------------------------------------------
    ("договор", "合同", "договор"),
    ("контракт", "外贸合同", "контракт"),
    ("инвойс", "商业发票", "инвойс"),
    ("упаковочный лист", "装箱单", "упаковочный лист"),
    ("накладная", "运单", "накладная"),
    ("коносамент", "提单", "коносамент"),
    ("приложение к договору", "合同附件", "приложение к договору"),
    # -- 常用表达 ---------------------------------------------------------
    ("Здравствуйте", "您好", "Здравствуйте"),
    ("Уважаемый", "尊敬的", "Уважаемый"),
    ("С уважением", "此致敬礼", "С уважением"),
    ("подтверждаем", "我们确认", "подтверждаем"),
    ("просим сообщить", "请告知", "просим сообщить"),
    ("запрос", "询价", "запрос"),
    ("предложение", "报价", "предложение"),
    ("заказ", "订单", "заказ"),
    # -- 商品与标识（实测发现引擎会把 артикул 译成「产材」）------------------
    ("артикул", "货号", "артикул"),
    ("наименование товара", "品名", "наименование товара"),
    ("код товара", "商品编码", "код товара"),
    ("единица измерения", "计量单位", "единица измерения"),
    ("производитель", "生产厂家", "производитель"),
    ("поставщик", "供应商", "поставщик"),
    ("покупатель", "买方", "покупатель"),
    # -- 价格（实测发现 руб. 被译成「红宝石」）------------------------------
    ("руб.", "卢布", "руб."),
    ("курс валюты", "汇率", "курс валюты"),
    # -- 重量与尺寸 -------------------------------------------------------
    ("вес нетто", "净重", "вес нетто"),
    ("вес брутто", "毛重", "вес брутто"),
    ("габариты", "外形尺寸", "габариты"),
    ("объём", "体积", "объём"),
    # -- 付款补充 ---------------------------------------------------------
    ("форма оплаты", "付款方式", "форма оплаты"),
    ("срок оплаты", "付款期限", "срок оплаты"),
    # -- 交货条件与运输方式 -----------------------------------------------
    ("условия поставки", "交货条件", "условия поставки"),
    ("железнодорожная перевозка", "铁路运输", "железнодорожная перевозка"),
    ("морская перевозка", "海运", "морская перевозка"),
    ("авиаперевозка", "空运", "авиаперевозка"),
    ("ФОБ", "离岸价", "ФОБ"),
    ("СИФ", "到岸价", "СИФ"),
    # -- 单证补充 ---------------------------------------------------------
    ("счёт-фактура", "发票", "счёт-фактура"),
    ("сертификат соответствия", "合格证", "сертификат соответствия"),
    ("ТН ВЭД", "海关编码", "ТН ВЭД"),
    # -- 索赔与保障 -------------------------------------------------------
    ("претензия", "索赔", "претензия"),
    ("штраф", "罚款", "штраф"),
    ("форс-мажор", "不可抗力", "форс-мажор"),
    ("гарантийный срок", "保修期", "гарантийный срок"),
    # -- 产品（用户实际做的品类）------------------------------------------
    ("спецодежда", "劳保服", "спецодежда"),
    ("перчатки", "手套", "перчатки"),

    # -- 城市（俄罗斯贸易文档里到处都是，从哪发货、发到哪）-------------------
    ("Москва", "莫斯科", "Москва"),
    ("Санкт-Петербург", "圣彼得堡", "Санкт-Петербург"),
    ("Новосибирск", "新西伯利亚", "Новосибирск"),
    ("Екатеринбург", "叶卡捷琳堡", "Екатеринбург"),
    ("Нижний Новгород", "下诺夫哥罗德", "Нижний Новгород"),
    ("Казань", "喀山", "Казань"),
    ("Челябинск", "车里雅宾斯克", "Челябинск"),
    ("Омск", "鄂木斯克", "Омск"),
    ("Самара", "萨马拉", "Самара"),
    ("Ростов-на-Дону", "顿河畔罗斯托夫", "Ростов-на-Дону"),
    ("Уфа", "乌法", "Уфа"),
    ("Красноярск", "克拉斯诺亚尔斯克", "Красноярск"),
    ("Воронеж", "沃罗涅日", "Воронеж"),
    ("Пермь", "彼尔姆", "Пермь"),
    ("Волгоград", "伏尔加格勒", "Волгоград"),
    ("Краснодар", "克拉斯诺达尔", "Краснодар"),
    ("Саратов", "萨拉托夫", "Саратов"),
    ("Тюмень", "秋明", "Тюмень"),
    ("Тольятти", "陶里亚蒂", "Тольятти"),
    ("Ижевск", "伊热夫斯克", "Ижевск"),
    ("Барнаул", "巴尔瑙尔", "Барнаул"),
    ("Ульяновск", "乌里扬诺夫斯克", "Ульяновск"),
    ("Иркутск", "伊尔库茨克", "Иркутск"),
    ("Хабаровск", "哈巴罗夫斯克", "Хабаровск"),
    ("Владивосток", "符拉迪沃斯托克", "Владивосток"),
    ("Ярославль", "雅罗斯拉夫尔", "Ярославль"),
    ("Махачкала", "马哈奇卡拉", "Махачкала"),
    ("Томск", "托木斯克", "Томск"),
    ("Новороссийск", "新罗西斯克", "Новороссийск"),
    ("Калининград", "加里宁格勒", "Калининград"),
]


#: Mistranslations that only exist in context. Measuring a term on its own cannot find
#: them, because the engine answers differently inside a sentence: "руб." alone comes back
#: as "鲁比", while the "Цена, руб." heading a price column comes back as "红宝石".
#:
#: Kept separate from CONCEPTS and labelled, because these are not the output of the loop
#: below - each one was written down by looking at a result. The fourth field says where it
#: came from, so the next person can tell a supplier's document from a measurement.
#:
#: (wrong Chinese, right Chinese, the term it came from, how it was found)
OBSERVED: list[tuple[str, str, str, str]] = [
    ("红宝石", "卢布", "руб.", "供应商报价单里实际见到的"),
    ("产材", "货号", "артикул", "供应商报价单里实际见到的"),
    # Found by translating twenty whole trade sentences and reading them. ГОСТ is the
    # Russian state standard and appears in nearly every specification; the engine renders
    # it as GATT, which is a different treaty entirely. The second is a meaning reversal:
    # "отсрочка платежа" is a payment period granted to a good customer, and 拖延 is what
    # you do to a debt you are avoiding.
    ("《全球货物和科技总协定》", "ГОСТ（俄罗斯国家标准）", "ГОСТ", "实测 20 句贸易俄文时读出来的"),
    ("拖延", "延期", "отсрочка", "实测 20 句贸易俄文时读出来的"),
]



#: Whole phrases, measured in the Chinese-to-Russian direction only. A term measured on
#: its own and the same term inside a sentence get different answers out of the engine,
#: and the phrase is where a business letter actually lives. Measured: 顺祝商祺 - the
#: sign-off at the foot of a letter - comes back as "Желаю вам удачи" (good luck with
#: that), and 您 comes back as "Ты", which is how you address a friend. Neither is a
#: translation difficulty; both are the kind of thing that ends a business relationship.
#:
#: (the Chinese the user types, the Russian a trade letter should carry)
PHRASES_ZH: list[tuple[str, str]] = [
    # -- 称呼与客气 -------------------------------------------------------
    ("你好", "Здравствуйте"),
    ("您", "Вы"),
    ("顺祝商祺", "С уважением"),
    ("感谢您的询价", "Благодарим за запрос"),
    ("感谢您的来信", "Благодарим за письмо"),
    ("期待与您合作", "Надеемся на сотрудничество"),
    ("期待您的回复", "Ждём вашего ответа"),
    ("请确认", "Просим подтвердить"),
    # -- 产品与交期 -------------------------------------------------------
    ("面料", "ткань"),
    ("交货期", "срок поставки"),
]


#: Engines every term is measured on, in order. Only the ones that need no credentials.
#:
#: Measuring on one engine was not enough: a rule keyed on the offline engine's mistake is
#: dead the moment the user switches engine, and the shipped file changed 3 of 20 cells on
#: the offline engine and 0 of 20 on MyMemory. Every engine here contributes its mistakes
#: to the same rule.
MEASURE_ENGINES = ("opus", "mymemory")



#: The city block at the tail of CONCEPTS. Cities are proper nouns, and that allows
#: something a trade term cannot have: whatever the engine writes, the answer is the same
#: word. So they also get a loose Latin match - see latin_catch_all.
CITIES: list[tuple[str, str, str]] = CONCEPTS[
    next(index for index, entry in enumerate(CONCEPTS) if entry[0] == "Москва"):]

#: Cyrillic to Latin, the way every Russian translation service does it.
_LATIN = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "kh", "ц": "ts",
    "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}

_VOWELS = "aeiouy"


def latin_catch_all(russian: str) -> str:
    """A pattern for the ways a model spells this name when it cannot translate it.

    An engine that does not know a place name does not translate it - it leaves it in
    Latin letters. Measured on a rendered contract: Новосибирск came back as "Newosibirsk"
    and Челябинск as "Chelabinski", and no Chinese rendering covers either, so the patch
    built from measuring the bare name never fired.

    Vowels are the part that moves: the same city arrives as Chelyabinsk and Chelabinski,
    and Новосибирск as Novosibirsk and Newosibirsk - the model inserts a vowel it did not
    hear. So a vowel may be present or absent, and any number of them may run together.
    Consonants stay, which is what keeps the pattern from matching unrelated words.
    """
    pieces: list[str] = []
    for char in russian.lower():
        latin = _LATIN.get(char)
        if latin is None:
            # A space or a hyphen: the model picks its own separator, or drops it.
            pieces.append(r"[\s\-]*")
            continue
        for letter in latin:
            if letter in _VOWELS:
                pieces.append("[aeiouy]*")
            elif letter == "v":
                # в is transliterated v or w depending on the service.
                pieces.append("[vw]")
            else:
                pieces.append(re.escape(letter))
    return "".join(pieces)


def city_rule(rules: list[dict], russian: str, chinese: str) -> dict:
    """The ru->zh rule for a city, created if the engines happened to get it right.

    A rule only exists to be found when measuring went wrong; when an engine read the bare
    name correctly no rule was written, and then a misread inside a sentence would go
    uncorrected. Cities are few enough that always carrying one costs nothing: a wrong
    rendering that never occurs simply never matches.
    """
    for rule in rules:
        if (rule.get("when") == russian and rule.get("side") == "target"
                and rule.get("pairs") == ["ru->zh"]):
            return rule
    rule = {"src": "", "dst": chinese, "mode": "regex", "side": "target",
            "pairs": ["ru->zh"], "when": russian,
            "note": "地名：单独测的时候引擎可能译对了，但句子里会写成拉丁字母"}
    rules.append(rule)
    return rule


#: The handful of cities whose English name is not a transliteration at all. Only the two
#: big ones are worth carrying; the rest are spelled the same way in every language.
EXONYMS = {
    "Москва": ["moscow"],
    "Санкт-Петербург": [r"saint[\s\-]*petersburg", r"st\.?[\s\-]*petersburg"],
}


def latin_alternation(russian: str) -> str:
    """Everything a city rule matches in Latin letters: transliteration, plus exonyms."""
    parts = [latin_catch_all(russian)]
    parts.extend(EXONYMS.get(russian, []))
    return "(?i:" + "|".join(parts) + ")"


def add_latin_catch_all(rule: dict, russian: str) -> bool:
    """Extend a city rule with the Latin alternation. True when it changed."""
    pattern = latin_alternation(russian)
    source = rule.get("src", "")
    if pattern in source:
        return False
    # Re-running must not stack alternations: drop whatever the last run added.
    source = re.sub(r"\|?\(\?i:(?:[^()]|\([^()]*\))*\)", "", source).strip("|")
    if source and rule.get("mode") != "regex":
        source = re.escape(source)
    rule["src"] = (source + "|" + pattern) if source else pattern
    rule["mode"] = "regex"
    note = rule.get("note", "")
    extra = "句子里引擎会把它写成拉丁字母，补一条元音宽松的匹配"
    rule["note"] = (note + "；" + extra) if note else extra
    return True


def patch_cities(rules: list[dict]) -> int:
    """Apply both halves to every city. Returns how many rules changed."""
    changed = 0
    for russian, chinese, _back in CITIES:
        if add_latin_catch_all(city_rule(rules, russian, chinese), russian):
            changed += 1
    return changed


def build_routes(src: str, tgt: str) -> list[tuple[str, object]]:
    """Every measurable engine for one direction, loaded and ready."""
    from app.config import AppConfig
    from app.engine import build

    routes: list[tuple[str, object]] = []
    for key in MEASURE_ENGINES:
        cfg = AppConfig()
        cfg.source_lang, cfg.target_lang = src, tgt
        cfg.translate.engine = key
        try:
            route = build(cfg, str(ROOT / "models"))
            route.load()
        except Exception as exc:  # noqa: BLE001
            print(f"  引擎 {key} 不可用，跳过：{exc}")
            continue
        routes.append((key, route))
    return routes


def combined_rule(renderings: dict[str, str], correct: str, term: str, pair: str) -> dict:
    """One rule per term, carrying every engine's version of the mistake.

    More than one rendering becomes a regular expression alternation, which is what the
    rule model already supports - so one entry still covers the term on every engine, and
    there is nothing engine-specific in the file.
    """
    wrong = list(renderings)
    if len(wrong) == 1:
        src, mode = wrong[0], "literal"
    else:
        src, mode = "|".join(re.escape(one) for one in wrong), "regex"
    note = "；".join(f"{renderings[one]} 译作「{one}」" for one in wrong)
    return {"src": src, "dst": correct, "mode": mode, "side": "target",
            "pairs": [pair], "when": term, "note": note}


def main() -> int:
    parser = argparse.ArgumentParser(description="生成中俄外贸内置词库")
    parser.add_argument("--dry-run", action="store_true", help="只测量，不写文件")
    args = parser.parse_args()

    from app import bootstrap

    bootstrap.prepare()

    from app.config import AppConfig
    from app.engine import build

    print("加载引擎…")
    routes_ru = build_routes("ru", "zh")
    routes_zh = build_routes("zh", "ru")
    if not routes_ru or not routes_zh:
        print("没有可用的引擎，退出")
        return 1
    print(f"就绪，参与测量的引擎：{[name for name, _ in routes_ru]}")
    print(f"开始逐条测量（{len(CONCEPTS)} 个术语，每条约 2-4 秒）\n")

    rules: list[dict] = []
    started = time.perf_counter()
    for index, (russian, chinese, back) in enumerate(CONCEPTS, start=1):
        # Measured in both forms, because an engine does not answer the same way for the
        # two: "артикул" on its own comes back as "电动", while the "Артикул" that heads a
        # price list comes back as "产材". Capitalised is the form that actually appears.
        forms = [russian]
        titled = russian[:1].upper() + russian[1:]
        if titled != russian:
            forms.append(titled)

        readings: list[tuple[str, str, str]] = []
        by_engine: dict[str, str] = {}
        for engine, route_ru in routes_ru:
            for form in forms:
                try:
                    text = route_ru.translate(form).strip()
                except Exception as exc:  # noqa: BLE001
                    text = f"<{exc}>"
                readings.append((engine, form, text))
                if text and not text.startswith("<") and text != chinese:
                    by_engine.setdefault(text, engine)

        back_engine: dict[str, str] = {}
        for engine, route_zh in routes_zh:
            try:
                text = route_zh.translate(chinese).strip()
            except Exception as exc:  # noqa: BLE001
                text = f"<{exc}>"
            if text and not text.startswith("<") and text != back:
                back_engine.setdefault(text, engine)

        print(f"[{index:>3}/{len(CONCEPTS)}] {russian}  →  期望：{chinese}")
        for engine, form, text in readings:
            mark = " " if text == chinese else "*"
            print(f"    {mark} {engine:<9} {form:<22} {text}")
        for text, engine in back_engine.items():
            print(f"    * {engine:<9} zh→ru {chinese:<12} {text}")

        # A rule only where an engine actually produced something else, and always with a
        # when condition naming the term it came from. Without that condition the rule
        # fires on any translation containing the Chinese it was keyed on, and short keys
        # like "顺序" or "电动" then rewrite ordinary sentences.
        if by_engine:
            rules.append(combined_rule(by_engine, chinese, russian, "ru->zh"))
        if back_engine:
            rules.append(combined_rule(back_engine, back, chinese, "zh->ru"))


    print(f"\n再测 {len(PHRASES_ZH)} 条商务短语（中译俄）\n")
    for index, (chinese, back) in enumerate(PHRASES_ZH, start=1):
        back_engine: dict[str, str] = {}
        for engine, route_zh in routes_zh:
            try:
                text = route_zh.translate(chinese).strip()
            except Exception as exc:  # noqa: BLE001
                text = f"<{exc}>"
            if text and not text.startswith("<") and text != back:
                back_engine.setdefault(text, engine)
        print(f"[短语 {index:>2}/{len(PHRASES_ZH)}] {chinese}  →  期望：{back}")
        for text, engine in back_engine.items():
            print(f"    * {engine:<9} {text}")
        if back_engine:
            rules.append(combined_rule(back_engine, back, chinese, "zh->ru"))

    for wrong, right, term, how in OBSERVED:
        rules.append({"src": wrong, "dst": right, "side": "target",
                      "pairs": ["ru->zh"], "when": term,
                      "note": f"上下文里的误译（原术语 {term}；{how}）"})

    cities = patch_cities(rules)
    print(f"\n城市名补了 {cities} 条拉丁字母兜底")

    elapsed = time.perf_counter() - started
    print(f"测量完成，用时 {elapsed / 60:.1f} 分钟")
    print(f"产出 {len(rules)} 条纠正规则（{len(CONCEPTS)} 个术语 + "
          f"{len(PHRASES_ZH)} 条商务短语 + "
          f"{len(OBSERVED)} 条实际观察到的误译）")
    print(f"每条规则覆盖 {len(routes_ru)} 个引擎的错法："
          f"{[name for name, _ in routes_ru]}")
    import collections
    regex_rules = sum(1 for one in rules if one.get("mode") == "regex")
    print(f"其中 {regex_rules} 条是多引擎合并的正则规则")

    if args.dry_run:
        for rule in rules[:20]:
            print(f"  [{rule['pairs'][0]}] {rule['src']} -> {rule['dst']}")
        return 0

    payload = {
        "version": 1,
        "_note": "中俄外贸内置词库，由 scripts/build_trade_glossary.py 实测生成："
                 "大多数规则是拿多个引擎（离线 + 免费联网）逐条翻出来、跟期望对比得到的，"
                 "把各引擎的错法合并进同一条规则，所以换引擎后仍然有效；"
                 "少数几条来自供应商文件里实际见到的误译（note 里写明了）。"
                 "每条规则都带 when 条件，只在原文出现该术语时才动手。"
                 "可以在程序里继续添加，你自己的规则会覆盖这里的。",
        "rules": rules,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {OUT.relative_to(ROOT)}（{OUT.stat().st_size / 1024:.0f} KB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
