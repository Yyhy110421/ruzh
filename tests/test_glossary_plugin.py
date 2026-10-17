"""Test the glossary and the user-plugin engine loading."""
import os
import shutil
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "glossary_plugin.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import engines  # noqa: E402
from app.config import AppConfig  # noqa: E402
from app.glossary import Glossary, Rule, GlossaryError  # noqa: E402

failures = 0


def check(condition: bool, message: str) -> None:
    global failures
    if condition:
        print(f"  [OK  ] {message}")
    else:
        print(f"  [FAIL] {message}")
        failures += 1


def test_glossary() -> None:
    print("=" * 72)
    print("词库")
    print("=" * 72)
    tmp = ROOT / "tests" / "glossary_test.json"
    tmp.unlink(missing_ok=True)

    g = Glossary()
    g.add(Rule(src="GPU", dst="显卡", case_sensitive=False))
    g.add(Rule(src=r"\bAI\b", dst="人工智能", mode="regex"))
    g.add(Rule(src="Kubernates", dst="Kubernetes", side="source"))
    g.add(Rule(src="Zhihu", dst="知乎", pairs=["en->zh"]))

    check(g.apply_target("The GPU is fast, and AI wins.", "en", "zh") ==
          "The 显卡 is fast, and 人工智能 wins.", "字母大小写不敏感 + 正则")
    check(g.apply_source("Deploy to Kubernates now", "en", "zh") ==
          "Deploy to Kubernetes now", "源侧规则在翻译前生效")
    check(g.apply_target("We use Zhihu.", "en", "zh") == "We use 知乎.", "限定语言对生效")
    check(g.apply_target("We use Zhihu.", "en", "ru") == "We use Zhihu.",
          "限定语言对时其它语言对不受影响")

    # round trip
    saved = g.save(tmp)
    again = Glossary.load(saved)
    check(len(again.rules) == 4, f"存盘再读回条数一致（{len(again.rules)}）")
    check(again.apply_target("GPU", "en", "zh") == "显卡", "读回后规则仍可用")

    # bad regex must be reported, not crash
    bad = Glossary()
    bad.add(Rule(src="([unclosed", dst="x", mode="regex"))
    check(not bad.rules[0].enabled, "非法正则被禁用而不是抛异常")
    check(any("正则无效" in p for p in bad.problems), "非法正则出现在 problems 里")

    # tsv
    tsv = ROOT / "tests" / "glossary_test.tsv"
    tsv.write_text("# 注释\nhello\t你好\nworld\t世界\n坏行没有制表符\n", encoding="utf-8")
    count = g.import_tsv(tsv)
    check(count == 2, f"TSV 导入只取合法行（导入 {count} 条）")
    check(any("不是「原文" in p for p in g.problems), "非法 TSV 行被记录为问题")

    # malformed json file
    broken = ROOT / "tests" / "glossary_broken.json"
    broken.write_text("{ this is not json", encoding="utf-8")
    try:
        Glossary.load(broken)
        check(False, "坏 JSON 应该报错")
    except GlossaryError:
        check(True, "坏 JSON 报出可读错误")

    for path in (tmp, tsv, broken):
        path.unlink(missing_ok=True)


def test_plugin() -> None:
    print()
    print("=" * 72)
    print("自定义引擎插件")
    print("=" * 72)

    folder = engines.plugins_dir()
    example = folder / "_example_dictionary.py"
    active = folder / "example_dictionary.py"

    check(folder.is_dir(), f"插件目录存在：{folder}")
    check(example.is_file(), "示例插件已生成（下划线开头，不会自动加载）")
    check((folder / "README.md").is_file(), "插件目录里有说明文档")

    before = set(engines.catalog())
    check("dictionary-demo" not in before, "示例插件默认不加载")

    shutil.copy2(example, active)
    try:
        engines.reload()
        after = engines.catalog()
        check("dictionary-demo" in after, "改名为正式插件后出现在引擎列表里")
        spec = after.get("dictionary-demo")
        if spec:
            check(spec.kind == "custom", "kind 标记为 custom")
            check(str(active) == spec.origin, "origin 记录了来源文件")
            cfg = AppConfig()
            cfg.source_lang, cfg.target_lang = "en", "zh"
            cfg.translate.engine = "dictionary-demo"
            route = engines.build(cfg, str(ROOT / "models"))
            route.load()
            out = route.translate("hello world")
            check(out == "你好 世界", f"插件真的翻译了：{out!r}")
            check(route.is_pivot is False, "插件引擎不会被标记为中转")
    finally:
        active.unlink(missing_ok=True)
        engines.reload()
    check("dictionary-demo" not in engines.catalog(), "移除插件后从列表消失")

    # a plugin with a syntax error must be reported, not fatal
    broken = folder / "broken_plugin.py"
    broken.write_text("def create(:\n    pass\n", encoding="utf-8")
    try:
        engines.reload()
        check("broken_plugin" not in engines.catalog(), "语法错误的插件不会进入列表")
        check(any("broken_plugin" in p.path for p in engines.problems()),
              "语法错误的插件被记录为加载问题")
    finally:
        broken.unlink(missing_ok=True)
        engines.reload()


def main() -> int:
    test_glossary()
    test_plugin()
    print()
    print("=" * 72)
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
