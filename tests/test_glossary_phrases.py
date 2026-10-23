"""The Chinese-to-Russian half of the built-in glossary.

The product is sold as a China-Russia tool, and half of that traffic goes the other way:
a Chinese trader writing a Russian reply. That direction had far less coverage than
Russian-to-Chinese, and measuring it found failures that are not translation difficulties
at all but etiquette ones - 您 (the polite "you") coming back as "Ты", which is how you
address a friend, and 顺祝商祺, a letter's sign-off, coming back as "Желаю вам удачи"
(good luck with that).

Each case below was measured: translate the Chinese with the engine, then check that the
shipped glossary turns the result into the Russian a trade letter should carry. A phrase
the engine already gets right is listed too, so that a rule which breaks it is caught.

Run: python tests/test_glossary_phrases.py
"""
import re
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "glossary_phrases.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


#: (what the user types, what a Russian business letter has to say)
PHRASES = [
    ("你好", "Здравствуйте"),
    ("您", "Вы"),
    ("顺祝商祺", "С уважением"),
    ("感谢您的询价", "Благодарим за запрос"),
    ("感谢您的来信", "Благодарим за письмо"),
    ("期待与您合作", "Надеемся на сотрудничество"),
    ("期待您的回复", "Ждём вашего ответа"),
    ("请确认", "Просим подтвердить"),
    ("面料", "ткань"),
    ("交货期", "срок поставки"),
    # Already right before the glossary existed; here so a bad rule cannot break them.
    ("您好", "Здравствуйте"),
    ("报价单", "прайс-лист"),
    ("样品", "образец"),
]

#: Words that must never reach a customer as the final answer in this direction.
FORBIDDEN = [
    ("您", "Ты"),
    ("顺祝商祺", "Желаю вам удачи"),
    ("面料", "Потрясающе"),
    ("报价单", "Кот"),
    ("交货期", "Период поставки"),
]


def main() -> int:
    from app.config import AppConfig
    from app.engine import build
    from app.glossary import load_with_builtin

    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "zh", "ru"
    cfg.translate.engine = "opus"

    route = build(cfg, str(ROOT / "models"))
    route.load()
    glossary = load_with_builtin(cfg.resolved_glossary_path())

    # Sections four and five are the other direction, so they need a route built the other
    # way round. Feeding Russian to the Chinese-to-Russian route produces Russian nonsense
    # and no rule fires, which is how the first version of this test managed to fail.
    cfg_back = AppConfig()
    cfg_back.source_lang, cfg_back.target_lang = "ru", "zh"
    cfg_back.translate.engine = "opus"
    route_back = build(cfg_back, str(ROOT / "models"))
    route_back.load()
    print(f"词库 {len(glossary.rules)} 条")

    zh_rules = [r for r in glossary.rules if "zh->ru" in (r.pairs or [])]
    print(f"其中中译俄方向 {len(zh_rules)} 条")
    check(len(zh_rules) > 40, f"中译俄方向有足够规则（{len(zh_rules)} 条）")
    print()

    print("=" * 78)
    print("一、引擎翻完，词库能不能救回来")
    print("=" * 78)
    fixed = 0
    for chinese, want in PHRASES:
        raw = route.translate(chinese).strip()
        after = glossary.apply_target(raw, "zh", "ru", source=chinese)
        ok = want.lower() in after.lower()
        if ok:
            fixed += 1
        check(ok, f"{chinese} → {want}",
              "" if ok else f"\n         引擎: {raw}\n         词库后: {after}")
    print(f"        救回 {fixed} / {len(PHRASES)}")
    print()

    print("=" * 78)
    print("二、绝不能让客户看到的结果")
    print("=" * 78)
    for chinese, forbidden in FORBIDDEN:
        raw = route.translate(chinese).strip()
        after = glossary.apply_target(raw, "zh", "ru", source=chinese)
        # Whole-word match: "Кот" is not present in "котировок" just because the
        # letters happen to be there. A substring check reports that as a failure.
        bound = r"(?<![\wЀ-ӿ])" + re.escape(forbidden) + r"(?![\wЀ-ӿ])"
        bad = re.search(bound, after, re.IGNORECASE) is not None
        check(not bad, f"「{chinese}」的结果里没有 {forbidden!r}",
              f"实际：{after}" if bad else f"→ {after}")
    print()

    print("=" * 78)
    print("三、方向是隔离的")
    print("=" * 78)
    # A rule belongs to one direction. The Chinese-to-Russian rules exist to rewrite the
    # Russian an engine produces from Chinese, and must not touch the Russian that came
    # from Russian - their when keys are Chinese, and none of them can match here.
    for text in ["Потрясающе", "Желаю вам удачи", "Ты", "Кот"]:
        after = glossary.apply_target(text, "ru", "zh", source="面料")
        check(after == text, f"中译俄规则没在俄译中方向动手：{text!r}",
              f"被改成 {after!r}" if after != text else "")

    # And the other way round: Russian text going Russian-to-Chinese is allowed to be
    # rewritten, but only into Chinese.
    for text in ["Срок поставки 30 дней", "Цена 1200 рублей"]:
        after = glossary.apply_target(text, "ru", "zh", source=text)
        has_cyrillic = any("\u0400" <= ch <= "\u04ff" for ch in after)
        check(has_cyrillic or after != text,
              f"俄译中方向的结果是中文或原样：{after!r}")
    print()

    print("=" * 78)
    print("四、俄罗斯城市名")
    print("=" * 78)
    # A trade document is mostly places: where the goods ship from, where they ship to.
    # Before the glossary covered them the engine produced 新闻社 for Новосибирск,
    # 人 for Воронеж, 双月 for Пермь and 暗暗 for Тюмень - Chinese words, all of them
    # wrong, and a reader has no way to tell.
    CITIES = {
        "Москва": "莫斯科",
        "Новосибирск": "新西伯利亚",
        "Екатеринбург": "叶卡捷琳堡",
        "Челябинск": "车里雅宾斯克",
        "Воронеж": "沃罗涅日",
        "Пермь": "彼尔姆",
        "Тюмень": "秋明",
        "Барнаул": "巴尔瑙尔",
        "Омск": "鄂木斯克",
        "Владивосток": "符拉迪沃斯托克",
        "Краснодар": "克拉斯诺达尔",
        "Нижний Новгород": "下诺夫哥罗德",
    }
    city_ok = 0
    for russian, chinese in CITIES.items():
        try:
            raw = route_back.translate(russian).strip()
            after = glossary.apply_target(raw, "ru", "zh", source=russian)
        except Exception as exc:  # noqa: BLE001
            raw = after = f"<{exc}>"
        hit = chinese in after
        if hit:
            city_ok += 1
        check(hit, f"{russian} → {chinese}",
              "" if hit else f"引擎: {raw}  词库后: {after}")
    check(city_ok == len(CITIES), f"城市名全对（{city_ok}/{len(CITIES)}）")
    print()

    print("=" * 78)
    print("五、ГОСТ 不能翻成关贸总协定")
    print("=" * 78)
    raw = route_back.translate("Товар соответствует требованиям ГОСТ.").strip()
    after = glossary.apply_target(raw, "ru", "zh",
                                  source="Товар соответствует требованиям ГОСТ.")
    check("ГОСТ" in after, f"ГОСТ 保住了：{after}")
    check("总协定" not in after, f"没有冒充关贸总协定：{after}")
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
