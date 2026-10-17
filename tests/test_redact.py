"""Masking the identifying parts of a message: what it catches, and what it must not.

Two failures matter here and they pull in opposite directions. Mask too little and the
customer's company name and bank details go to a stranger's server, which is the whole
reason the feature exists. Mask too much and the translation is worthless - a price, a
quantity or a delivery date turned into [[P1]] is not a privacy win, it is a broken
translation. So most of what follows is about what must be left alone.

Run: python tests/test_redact.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "redact.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.redact import RULES, Redactor  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


#: (kind, text that must be caught)
CAUGHT = [
    ("E", "пишите на zakaz@romashka.ru пожалуйста"),
    ("E", "邮箱 sales@yangganju.com"),
    ("U", "сайт www.romashka.ru"),
    ("U", "подробности на https://romashka.ru/price?id=3 тут"),
    ("T", "Наш ИНН 7707083893 для счёта"),
    ("S", "统一社会信用代码 91110108MA01ABCD2X"),
    ("P", "+7 (495) 123-45-67"),
    ("P", "电话 13812345678 可以打"),
    ("P", "8 916 123 45 67"),
    ("C", "ООО Ромашка просит прайс"),
    ("C", "Счёт на ООО «Ромашка и К» готов"),
    ("C", "北京洋甘菊贸易有限公司报价"),
    ("C", "上海机械集团发货"),
]

#: Text that must come back untouched. A price is not a phone number and a date is not a
#: bank account, however much they look alike digit for digit.
UNTOUCHED = [
    "Цена 1 200 руб. за тонну.",
    "Срок поставки 30 календарных дней.",
    "Дата 2026-10-14, объём 500 тонн.",
    "Итого 600 000 руб. с НДС.",
    "单价 1200 卢布，起订量 500 件，交货期 30 天。",
    "总金额 600000 卢布，付款 30%。",
    "Позиция № 15, количество 8 штук.",
    "Здравствуйте! Направляем коммерческое предложение.",
]


def main() -> int:
    redactor = Redactor()

    print("=" * 78)
    print("一、该遮的遮没遮住")
    print("=" * 78)
    for want_code, text in CAUGHT:
        found = redactor.scan(text)
        codes = [f.code for f in found]
        ok = want_code in codes
        label = next((r.label for r in RULES if r.code == want_code), want_code)
        if ok:
            hit = next(f for f in found if f.code == want_code)
            check(True, f"{label}: {hit.text!r}")
        else:
            check(False, f"{label} 没认出来：{text!r}", f"只找到 {codes or '无'}")
    print()

    print("=" * 78)
    print("二、不该遮的一个都别碰")
    print("=" * 78)
    for text in UNTOUCHED:
        found = redactor.scan(text)
        check(not found, f"没动：{text[:44]}",
              f"但遮了 {[f.describe() for f in found]}" if found else "")
    print()

    print("=" * 78)
    print("三、遮完能一字不差地还原")
    print("=" * 78)
    ROUND_TRIP = [
        "Здравствуйте! ООО Ромашка просит прайс-лист. Наш ИНН 7707083893, "
        "телефон +7 (495) 123-45-67, почта zakaz@romashka.ru.",
        "您好！北京洋甘菊贸易有限公司报价如下。电话 13812345678，"
        "邮箱 sales@yangganju.com。统一社会信用代码 91110108MA01ABCD2X。",
        "Счёт выставлен на ООО «Ромашка и К». Оплата по договору № 15.",
        "Цена 1 200 руб. Ничего секретного тут нет.",
    ]
    for text in ROUND_TRIP:
        masked, mapping = redactor.mask(text)
        back, missing = Redactor.unmask(masked, mapping)
        check(back == text and not missing, f"往返一致（遮了 {len(mapping)} 处）",
              f"\n         发出: {masked}\n         还原: {back}\n         丢失: {missing}"
              if back != text or missing else "")
    print()

    print("=" * 78)
    print("四、同一个东西只占一个号（同一份文件里译法才一致）")
    print("=" * 78)
    twice = "ООО Ромашка пишет. ООО Ромашка ждёт ответа."
    masked, mapping = redactor.mask(twice)
    check(len(mapping) == 1, f"出现两次只记一个（{len(mapping)} 个）", masked)
    back, _ = Redactor.unmask(masked, mapping)
    check(back == twice, "两处都还原成公司名")
    print()

    print("=" * 78)
    print("五、引擎要是把占位符弄丢了，必须报出来")
    print("=" * 78)
    masked, mapping = redactor.mask("ООО Ромашка и ООО Берёза тут")
    # Pretend the engine dropped one of them.
    dropped = next(iter(mapping))
    eaten = masked.replace(dropped, "")
    back, missing = Redactor.unmask(eaten, mapping)
    check(missing == [dropped], f"丢的那个被报出来了：{missing}")
    print(f"        译文里少了 {dropped}，界面会提示「有 N 处敏感信息没能还原」")
    print()

    print("=" * 78)
    print("六、引擎给占位符加了空格也能认")
    print("=" * 78)
    spaced, missing = Redactor.unmask("Здравствуйте! [[ C1 ]] пишет.",
                                      {"[[C1]]": "ООО Ромашка"})
    check(spaced == "Здравствуйте! ООО Ромашка пишет." and not missing,
          f"空格版也还原了：{spaced}")
    print()

    print("=" * 78)
    print("七、什么时候才该开脱敏")
    print("=" * 78)
    from app import engine as engine_mod  # noqa: E402
    from app import engines as engines_mod  # noqa: E402
    from app.config import AppConfig  # noqa: E402
    from app.text_tool import Translator  # noqa: E402

    cfg = AppConfig()
    for engine_id in ("opus", "mymemory", "youdao", "baidu", "openai-compatible"):
        cfg.translate.engine = engine_id
        resolved = engines_mod.resolve_id(engine_id, cfg.resolved_models_root())
        print(f"        {engine_id:<20} 联网={engine_mod.is_online(resolved)}")

    # Building a Translator is what actually decides this, so build one. The online ones
    # need no model, so this stays quick; the offline one loads a model, which is the
    # price of testing the path that matters.
    cfg.translate.engine = "mymemory"
    cfg.redact_enabled = True
    online_unit = Translator(cfg, cfg.resolved_models_root())
    check(online_unit.redactor is not None, "联网引擎：脱敏开着")

    cfg.redact_enabled = False
    off_unit = Translator(cfg, cfg.resolved_models_root())
    check(off_unit.redactor is None, "关掉开关：不遮")

    cfg.redact_enabled = True
    cfg.translate.engine = "opus"
    local_unit = Translator(cfg, cfg.resolved_models_root())
    check(local_unit.redactor is None, "离线引擎：不遮（它根本不出本机）")
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
