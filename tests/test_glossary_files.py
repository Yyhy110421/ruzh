"""A customer's term list, in and out of the product.

This is the thing the product is sold on for enterprise work: every customer gets their
own terms. So the file path has to work in both directions and in the format the customer
actually has - which is a spreadsheet, not tab-separated text.

Two failures are covered here, one of each kind:

* export and import were not inverses. Seven columns were written and two were read, so a
  regex rule came back literal, a source-side rule came back patching the translation, and
  a rule the customer had switched off came back on - all silently.
* only .tsv could be imported at all, which meant asking a customer to convert their Excel
  sheet before the product would accept it.

Run: python tests/test_glossary_files.py
"""
import csv
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "glossary_files.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.glossary import Glossary, GlossaryError, Rule  # noqa: E402

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


#: One of each thing the format carries. Anything the exporter writes and the importer
#: drops shows up as a difference.
SAMPLE = [
    Rule(src="АКБ", dst="蓄电池", mode="literal", side="target",
         case_sensitive=False, enabled=True, when=""),
    Rule(src=r"\bООО\s+(\S+)", dst=r"\1 有限责任公司", mode="regex", side="target",
         case_sensitive=True, enabled=True, when="ООО"),
    Rule(src="спецодежды", dst="спецодежда", mode="literal", side="source",
         case_sensitive=False, enabled=True, when=""),
    Rule(src="старое", dst="旧的", mode="literal", side="target",
         case_sensitive=True, enabled=False, when=""),
]


def make_minimal_xlsx(path: Path, rows: list[list[str]]) -> None:
    """A workbook with two columns, written by hand.

    Only the parts the reader looks at are produced - xl/worksheets/sheet1.xml - because
    that is all read_cells opens. Strings are inline, not in a shared table, which is the
    layout Excel does not write and which therefore has no fixture anywhere else.
    """
    import zipfile

    def escape(text: str) -> str:
        return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    body = []
    for row_number, row in enumerate(rows, 1):
        cells = []
        for column, value in enumerate(row):
            reference = f"{chr(ord('A') + column)}{row_number}"
            cells.append(f'<c r="{reference}" t="inlineStr">'
                         f"<is><t>{escape(value)}</t></is></c>")
        body.append(f'<row r="{row_number}">' + "".join(cells) + "</row>")

    sheet = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             "<sheetData>" + "".join(body) + "</sheetData></worksheet>")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/worksheets/sheet1.xml", sheet)


def key(rule: Rule) -> tuple:
    return (rule.src, rule.dst, rule.mode, rule.side,
            bool(rule.case_sensitive), bool(rule.enabled), rule.when)


def main() -> int:
    work = ROOT / "tests" / "glossary_files_work"
    work.mkdir(parents=True, exist_ok=True)

    print("=" * 78)
    print("一、导出去再导回来，一模一样")
    print("=" * 78)
    tsv = work / "terms.tsv"
    written = Glossary(list(SAMPLE)).export_tsv(tsv)
    check(written == len(SAMPLE), f"导出 {written} 条")

    back = Glossary([])
    added = back.import_tsv(tsv)
    check(added == len(SAMPLE), f"导入 {added} 条")
    if len(back.rules) == len(SAMPLE):
        for want, got in zip(SAMPLE, back.rules):
            same = key(want) == key(got)
            check(same, f"{want.src!r}",
                  "" if same else f"\n         写出去: {key(want)}\n         读回来: {key(got)}")
    else:
        check(False, f"条数对不上 {len(back.rules)} vs {len(SAMPLE)}")

    print("        行为验证（不看字段看结果）")
    text = "Поставщик: ООО Ромашка"
    out = back.apply_target("Поставщик: ООО Ромашка", "ru", "zh", source=text)
    check(out != text, f"正则规则导回来还在干活：{out!r}")
    off = back.apply_target("这是 старое 的东西", "ru", "zh", source="这是 старое 的东西")
    check("旧的" not in off, f"关掉的规则没被打开：{off!r}")
    print()

    print("=" * 78)
    print("二、客户发来 CSV")
    print("=" * 78)
    # The realistic case: one file holding both scripts, which can only be UTF-8.
    csv_path = work / "客户术语.csv"
    rows = [["原文", "译文"],
            ["Костюм рабочий", "工作服"],
            ["Цена, руб.", "价格（卢布）"],
            ["", ""],
            ["Спецодежда", "劳保用品"]]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerows(rows)

    from_csv = Glossary([])
    n = from_csv.import_table(csv_path)
    check(n == 3, f"导入 {n} 条（表头跳过、空行跳过）")
    srcs = [r.src for r in from_csv.rules]
    check("Цена, руб." in srcs, f"带逗号的术语读对了：{srcs}")
    check("原文" not in srcs, "表头没被当成术语")

    # And a Chinese-only list from a Chinese Excel, which writes GBK.
    gbk_path = work / "中文术语.csv"
    with gbk_path.open("w", encoding="gbk", newline="") as handle:
        csv.writer(handle).writerows([["原文", "译文"],
                                      ["工作服", "Костюм рабочий"],
                                      ["劳保用品", "Спецодежда"]])
    from_gbk = Glossary([])
    n = from_gbk.import_table(gbk_path)
    check(n == 2, f"GBK 文件导入 {n} 条")
    check([r.src for r in from_gbk.rules] == ["工作服", "劳保用品"],
          f"GBK 中文读对了：{[r.src for r in from_gbk.rules]}")
    print()

    print("=" * 78)
    print("三、客户发来 Excel")
    print("=" * 78)
    xlsx = work / "客户术语.xlsx"
    # Built by hand rather than with openpyxl, which is not a dependency of this project.
    # Inline strings are the layout Excel itself does *not* write, so this also covers the
    # half of read_cells that the real fixtures never exercise.
    make_minimal_xlsx(xlsx, [["Термин", "Перевод"],
                             ["АКБ", "蓄电池"],
                             ["оборудование", "设备"],
                             ["поставка", "供货"]])
    from_xlsx = Glossary([])
    n = from_xlsx.import_table(xlsx)
    check(n == 3, f"导入 {n} 条")
    srcs = [r.src for r in from_xlsx.rules]
    dsts = [r.dst for r in from_xlsx.rules]
    check(srcs == ["АКБ", "оборудование", "поставка"], f"原文读对：{srcs}")
    check(dsts == ["蓄电池", "设备", "供货"], f"译文读对：{dsts}")
    check("Термин" not in srcs, "俄文表头没被当成术语")
    print()

    print("=" * 78)
    print("四、认不出的格式要明说，不能当成功")
    print("=" * 78)
    bad = work / "别的.pdf"
    bad.write_bytes(b"%PDF-1.4")
    try:
        Glossary([]).import_table(bad)
        check(False, "PDF 应该被拒绝")
    except GlossaryError as exc:
        check(True, f"拒绝并说明原因：{exc}")
    except Exception as exc:  # noqa: BLE001
        check(False, f"抛了别的异常：{type(exc).__name__}: {exc}")
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
