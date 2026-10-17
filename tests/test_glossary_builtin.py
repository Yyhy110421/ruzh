"""The shipped trade glossary has to actually reach the translators.

It did not. The 124 built-in terms sat in app/data/trade_glossary.json while every
translation path loaded only the user's own file:

    text_tool.load_glossary   -> Glossary.load(user_file)
    pipeline._ensure_glossary -> Glossary.load(user_file)

load_with_builtin() - the only loader that merges the built-in file - was called from
nothing but a test. So the file was advertised in the handover and did nothing in the
product: a fresh install translated "прайс-лист" with no trade terms at all.

The test drives both real entry points rather than the loader, because the bug was never
in the loader - it was in who called it.

Run: python tests/test_glossary_builtin.py
"""
import json
import sys
import tempfile
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "glossary_builtin.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.glossary import (Glossary, Rule, load_with_builtin,  # noqa: E402
                          trade_glossary_path)
from app.pipeline import Pipeline  # noqa: E402
from app.text_tool import load_glossary  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


class FakePipeline:
    """Just enough of a Pipeline for _ensure_glossary, which is the part under test."""

    def __init__(self) -> None:
        self._glossary = None
        self._glossary_key = None
        self.messages: list[str] = []

    def _error(self, message: str) -> None:
        self.messages.append(message)

    def _status(self, message: str) -> None:
        self.messages.append(message)


def main() -> int:
    print("=" * 78)
    print("一、内置词库文件本身")
    print("=" * 78)
    builtin_path = trade_glossary_path()
    print(f"        {builtin_path}")
    check(builtin_path.is_file(), "内置词库文件存在")
    if not builtin_path.is_file():
        print("\n没有内置词库，后面的检查没有意义")
        return 1
    builtin = Glossary.load(builtin_path)
    print(f"        内置 {len(builtin.rules)} 条规则")
    check(len(builtin.rules) > 0, "内置词库里有规则")
    print()

    work = ROOT / "tests" / "glossary_builtin_work"
    work.mkdir(parents=True, exist_ok=True)
    user_path = work / "user.json"
    # One rule of the user's own, so merging can be told apart from replacing.
    user_path.write_text(json.dumps({
        "version": 1,
        "rules": [{"src": "МОЙТЕРМ", "dst": "我的术语", "side": "target",
                   "pairs": ["ru->zh"]}],
    }, ensure_ascii=False), encoding="utf-8")

    print("=" * 78)
    print("二、文本/文档这条路：text_tool.load_glossary")
    print("=" * 78)
    cfg = AppConfig()
    cfg.glossary_enabled = True
    cfg.glossary_path = str(user_path)
    loaded = load_glossary(cfg)
    total = len(loaded.rules) if loaded else 0
    print(f"        加载到 {total} 条（内置 {len(builtin.rules)} + 用户 1）")
    check(total == len(builtin.rules) + 1,
          f"内置词库被合进来了（期望 {len(builtin.rules) + 1} 条，实际 {total}）")
    check(any(r.src == "МОЙТЕРМ" for r in (loaded.rules if loaded else [])),
          "用户自己的规则也在")
    if loaded and loaded.rules:
        check(loaded.rules[-1].src == "МОЙТЕРМ",
              "用户规则排在最后（同一条冲突时用户赢）")
    print()

    print("=" * 78)
    print("三、屏幕翻译这条路：pipeline._ensure_glossary")
    print("=" * 78)
    fake = FakePipeline()
    got = Pipeline._ensure_glossary(fake, cfg)
    total = len(got.rules) if got else 0
    print(f"        加载到 {total} 条")
    check(total == len(builtin.rules) + 1,
          f"内置词库被合进来了（期望 {len(builtin.rules) + 1} 条，实际 {total}）")
    print()

    print("=" * 78)
    print("四、内置规则真的会改到译文")
    print("=" * 78)
    # A rule from the shipped file, applied the way a translation applies it.
    fired = []
    for rule in builtin.rules:
        if rule.side != "target" or "ru->zh" not in (rule.pairs or ["ru->zh"]):
            continue
        if rule.src.strip() and rule.src != rule.dst:
            # The condition has to be satisfied, the way it is in a real translation: a
            # rule that carries one is skipped when no source is given at all.
            once = builtin.apply_target(rule.src, "ru", "zh",
                                        source=rule.when or rule.src)
            if once and once != rule.src:
                fired.append((rule.src, once))
    print(f"        拿内置规则自己的 src 去套，{len(fired)} 条生效")
    for src, dst in fired[:5]:
        print(f"          {src!r} -> {dst!r}")
    check(bool(fired), "内置规则能改到文字")
    print()

    print("=" * 78)
    print("五、带条件的规则只在原文出现该术语时才动手")
    print("=" * 78)
    # A target rule without a condition fires on any translation containing its key. The
    # shipped rules key on whatever the engine emitted, which is frequently a short common
    # word, so an unguarded "顺序 -> 订单" would rewrite every ordinary "顺序".
    probe = Glossary(rules=[Rule(src="顺序", dst="订单", side="target",
                                 pairs=["ru->zh"], when="заказ")])
    guarded = probe.apply_target("请按顺序发货", "ru", "zh", source="Отгрузка по заказу.")
    unguarded = probe.apply_target("请按顺序发货", "ru", "zh")
    print(f"        原文含 заказ 时：{guarded!r}")
    print(f"        原文不含时：      {unguarded!r}")
    check(guarded == "请按订单发货", "原文出现术语时规则生效")
    check(unguarded == "请按顺序发货", "原文没出现时规则不动手")
    mixed = probe.apply_target("请按顺序发货", "ru", "zh", source="Заказ №5 оформлен.")
    check(mixed == "请按订单发货", "大小写不同也算命中（Заказ / заказ）")

    # Russian inflects, so the dictionary form a rule is written with almost never appears
    # as-is in a document. Matching it exactly would mean the rule silently never helps.
    inflected = Glossary(rules=[Rule(src="服装", dst="劳保服", side="target",
                                     pairs=["ru->zh"], when="спецодежда")])
    source = "Коммерческое предложение на поставку спецодежды"
    got = inflected.apply_target("服装", "ru", "zh", source=source)
    print(f"        原文用变格 {source!r}")
    print(f"        规则条件写的是 спецодежда，结果：{got!r}")
    check(got == "劳保服", "变格形式（спецодежды / спецодежда）也算命中")

    # 中文不变化，所以中文条件按原样匹配，不截词尾。
    chinese = Glossary(rules=[Rule(src="Цена", dst="цена", side="target",
                                   pairs=["zh->ru"], when="价格")])
    zh_source = "价格与总价"
    hit = chinese.apply_target("Цена", "zh", "ru", source=zh_source)
    check(hit == "цена", "中文条件照常命中")
    miss = chinese.apply_target("Цена", "zh", "ru", source="交货期")
    check(miss == "Цена", "中文条件在原文不含时不动手")
    print()

    print("=" * 78)
    print("六、关掉词库开关时，什么都不加载")
    print("=" * 78)
    off = AppConfig()
    off.glossary_enabled = False
    off.glossary_path = str(user_path)
    check(load_glossary(off) is None, "开关关掉时 text_tool 不加载")
    fake2 = FakePipeline()
    check(Pipeline._ensure_glossary(fake2, off) is None, "开关关掉时 pipeline 不加载")
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
