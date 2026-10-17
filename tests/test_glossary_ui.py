"""Does the glossary table actually manage the user's own rules?

Adding a term was already possible; seeing and removing it was not, which makes a
glossary of any size unmanageable. This drives the table through add, list, delete and
save, and checks that the user's rules take precedence over the built-in ones.
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "glossary_ui.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import config as config_mod  # noqa: E402

failures = 0


def check(condition: bool, label: str) -> None:
    global failures
    if condition:
        print(f"  [OK  ] {label}")
    else:
        failures += 1
        print(f"  [FAIL] {label}")


def main() -> int:
    config_mod.ensure_dirs()
    cfg = config_mod.load()
    cfg.translate.engine = "opus"

    # Start from a clean user glossary so the counts are predictable.
    user_path = Path(cfg.resolved_glossary_path())
    backup = None
    if user_path.exists():
        backup = user_path.read_text(encoding="utf-8")
        user_path.unlink()

    from app.glossary import Glossary, Rule, load_with_builtin
    from app.ui import App, SettingsWindow

    app = App(cfg)
    app.update()
    window = SettingsWindow(app)
    window.update()
    notebook = window.winfo_children()[0]
    notebook.select(1)                        # the 词库 page
    window.update()

    print("表格初始状态")
    check(hasattr(window, "rule_table"), "有规则表格")
    builtin_rows = [i for i in window.rule_table.get_children() if i.startswith("builtin:")]
    mine_rows = [i for i in window.rule_table.get_children() if i.startswith("mine:")]
    check(len(builtin_rows) > 100, f"内置规则已列出（{len(builtin_rows)} 条）")
    check(len(mine_rows) == 0, "开始时没有我自己的规则")

    print("\n添加规则")
    for src, dst in (("АКБ", "蓄电池"), ("спецодежда", "工作服"), ("тонна", "吨")):
        window.rule_src_var.set(src)
        window.rule_dst_var.set(dst)
        window._add_rule()
    window.update()
    mine_rows = [i for i in window.rule_table.get_children() if i.startswith("mine:")]
    check(len(mine_rows) == 3, f"表格显示了我加的 3 条（实际 {len(mine_rows)}）")
    check(Glossary.load(user_path).stats()["total"] == 3, "文件里确实存了 3 条")

    print("\n删除一条")
    window.rule_table.selection_set("mine:1")
    # Bypass the confirmation dialog, which would block a headless run.
    import tkinter.messagebox as mb

    original = mb.askyesno
    mb.askyesno = lambda *a, **k: True
    try:
        window._delete_rules()
    finally:
        mb.askyesno = original
    window.update()
    remaining = Glossary.load(user_path)
    check(remaining.stats()["total"] == 2, f"文件里剩 2 条（实际 {remaining.stats()['total']}）")
    check([r.src for r in remaining.rules] == ["АКБ", "тонна"],
          f"删掉的是中间那条：{[r.src for r in remaining.rules]}")

    print("\n内置规则不能删")
    window.rule_table.selection_set("builtin:0")
    mb.showinfo = lambda *a, **k: None
    try:
        window._delete_rules()
    finally:
        pass
    check(Glossary.load(user_path).stats()["total"] == 2, "选了内置规则后文件没被动")

    print("\n表格里看得见生效条件和说明")
    rows = window.rule_table.get_children()
    # Column order is (src, dst, pairs, when, note, origin).
    conditioned = [i for i in rows
                   if window.rule_table.item(i, "values")[3] not in ("", "—")]
    plain = [i for i in rows if window.rule_table.item(i, "values")[3] == "—"]
    check(len(conditioned) > 100, f"带条件的规则都显示出来了（{len(conditioned)} 条）")
    check(bool(plain), f"没条件的规则显示为破折号（{len(plain)} 条）")
    example = window.rule_table.item(conditioned[0], "values")
    check(bool(example[4]), f"说明那一列有内容：{example[4]!r}")
    print(f"        例：{example[0]!r} -> {example[1]!r}，生效条件 {example[3]!r}")
    # A rule reading 顺序 -> 订单 is only defensible if the condition next to it is
    # visible; that is the whole reason the column exists.
    guarded = [window.rule_table.item(i, "values") for i in rows
               if window.rule_table.item(i, "values")[1] == "订单"]
    check(bool(guarded) and all(row[3] for row in guarded),
          f"看着可疑的规则都带着条件：{[r[3] for r in guarded]}")

    print("\n筛选")
    window.glossary_filter_var.set("заказ")
    window.update()
    filtered = window.rule_table.get_children()
    check(0 < len(filtered) < len(rows), f"筛选后行数变少（{len(rows)} -> {len(filtered)}）")
    check(all("заказ" in " ".join(window.rule_table.item(i, "values")).lower()
              for i in filtered), "留下的每一行都跟关键词有关")
    window.glossary_filter_var.set("")
    window.update()
    check(len(window.rule_table.get_children()) == len(rows), "清空筛选后恢复原样")

    print("\n我的规则优先于内置")
    combined = load_with_builtin(user_path, include_builtin=True)
    check(combined.stats()["total"] > 100, f"合并后共 {combined.stats()['total']} 条")
    ids = [i for i, _ in combined.rules_for("ru", "zh", "target")]
    sources = [combined.rules[i].src for i in ids]
    check(sources.index("АКБ") > sources.index("цена") if "цена" in sources else True,
          "我加的规则排在内置之后（后应用，优先级更高）")

    print("\n清理")
    window._close()
    app._on_close()
    user_path.unlink(missing_ok=True)
    if backup is not None:
        user_path.write_text(backup, encoding="utf-8")
        check(True, "原有的词库文件已还原")
    else:
        check(True, "测试用的词库文件已清理")

    print(f"\n失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
