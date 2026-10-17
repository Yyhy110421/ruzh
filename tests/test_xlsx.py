"""Spreadsheet translation: what must survive, and what must change.

Two fixtures, because the two string layouts are both common in the wild and only
exercising one of them would leave the other silently untranslated:

* tests/fixtures/报价单.xlsx is a real file written by openpyxl, which puts the text
  **inline** in each cell (t="inlineStr").
* the second fixture is built here with a **shared string table**, the layout Excel itself
  writes, where every cell holds an index into xl/sharedStrings.xml.

Run: python tests/test_xlsx.py
"""
import sys
import traceback
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "xlsx.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.xlsx_io import (SHARED_STRINGS, read_cells, read_strings,  # noqa: E402
                         translate_xlsx)

SAMPLE = ROOT / "tests" / "fixtures" / "报价单.xlsx"
WORK = ROOT / "tests" / "xlsx_work"
SHARED_SOURCE = WORK / "shared.xlsx"
SHARED_OUTPUT = WORK / "shared.zh.xlsx"
INLINE_OUTPUT = WORK / "inline.zh.xlsx"

S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

#: Parts that carry no text. A translator that rewrites one of these has damaged the file.
UNTOUCHED = ("xl/styles.xml", "xl/theme/theme1.xml", "docProps/core.xml", "docProps/app.xml")

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


def fake(mark: str = "【") -> object:
    """A translator that records what it was asked, so deduplication can be counted."""
    seen: list[str] = []

    def translate(text: str) -> str:
        seen.append(text)
        return mark + text + "】"

    translate.seen = seen  # type: ignore[attr-defined]
    return translate


def build_shared_strings_workbook(path: Path) -> None:
    """A minimal workbook in Excel's own layout: text in the table, cells hold indices."""
    path.parent.mkdir(parents=True, exist_ok=True)
    strings = ["Артикул", "Наименование", "Срок поставки — 30 дней.", "Артикул"]
    table = "".join(f"<si><t>{text}</t></si>" for text in strings)
    rows = (
        '<row r="1">'
        '<c r="A1" t="s"><v>0</v></c>'
        '<c r="B1" t="s"><v>1</v></c>'
        "</row>"
        '<row r="2">'
        '<c r="A2" t="s"><v>2</v></c>'
        '<c r="B2" t="n"><v>42</v></c>'
        "</row>"
        # A cell pointing at the same entry as A1: it must pick up the same translation.
        '<row r="3"><c r="A3" t="s"><v>3</v></c></row>'
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<worksheet xmlns="{NS}"><sheetData>{rows}</sheetData></worksheet>'
    )
    shared = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<sst xmlns="{NS}" count="{len(strings)}" uniqueCount="{len(strings)}">'
        f"{table}</sst>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<workbook xmlns="{NS}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings" Target="sharedStrings.xml"/>'
        "</Relationships>"
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("xl/workbook.xml", workbook)
        archive.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        archive.writestr("xl/worksheets/sheet1.xml", sheet)
        archive.writestr(SHARED_STRINGS, shared)


def part(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


# read_cells is the module's own reader, so the test both uses and covers it.
cell_values = read_cells


def main() -> int:
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"

    print("=" * 78)
    print("一、内联字符串（openpyxl 写出来的真实文件）")
    print("=" * 78)
    check(SAMPLE.is_file(), f"样本存在：{SAMPLE.name}")
    before = read_strings(SAMPLE)
    print(f"        读到的字符串 {len(before)} 条，例如 {before[:3]}")
    check(len(before) >= 8, f"读出了全部文字（{len(before)} 条）")

    engine = fake()
    result = translate_xlsx(cfg, "", SAMPLE, INLINE_OUTPUT, translator=engine)
    print(f"        翻译 {result.strings} 条，覆盖 {result.cells} 个单元格，"
          f"失败 {len(result.failed)} 处")
    check(result.failed == [], "没有失败")
    check(result.strings == len([t for t in before if t.strip()]), "每条非空字符串都翻了")

    after = cell_values(INLINE_OUTPUT)
    print(f"        翻译后 A1 = {after.get('A1')!r}")
    print(f"        翻译后 B4 = {after.get('B4')!r}")
    print(f"        翻译后 C4 = {after.get('C4')!r}  (数字)")
    check(after.get("A1", "").startswith("【"), "标题被翻了")
    check(after.get("B4", "").startswith("【"), "商品名被翻了")
    check(after.get("C4") == "500", "数字没被动过")
    check(after.get("D4") == "1200", "价格没被动过")
    print()

    print("=" * 78)
    print("二、除了文字，其它部分必须一个字节都没变")
    print("=" * 78)
    for name in UNTOUCHED:
        check(part(SAMPLE, name) == part(INLINE_OUTPUT, name), f"{name} 原样")
    with zipfile.ZipFile(SAMPLE) as a, zipfile.ZipFile(INLINE_OUTPUT) as b:
        check(sorted(a.namelist()) == sorted(b.namelist()), "包内文件清单一模一样")
    print()

    print("=" * 78)
    print("三、读写出来的还是合法 XML")
    print("=" * 78)
    for name in ("xl/worksheets/sheet1.xml",):
        try:
            ET.fromstring(part(INLINE_OUTPUT, name))
            check(True, f"{name} 合法")
        except ET.ParseError as exc:
            check(False, f"{name} 合法", str(exc))
    print()

    print("=" * 78)
    print("四、共享字符串表（Excel 自己的写法）")
    print("=" * 78)
    build_shared_strings_workbook(SHARED_SOURCE)
    engine = fake("《")
    result = translate_xlsx(cfg, "", SHARED_SOURCE, SHARED_OUTPUT, translator=engine)
    values = cell_values(SHARED_OUTPUT)
    for ref in ("A1", "B1", "A2", "A3", "B2"):
        print(f"        {ref} = {values.get(ref)!r}")
    check(values.get("A1", "").startswith("《"), "共享字符串被翻了")
    check(values.get("A2", "").startswith("《"), "第二条也被翻了")
    check(values.get("B2") == "42", "数字没被动过")
    check(values.get("A1") == values.get("A3"), "引用同一条的两个单元格拿到同一份译文")
    print()

    print("=" * 78)
    print("五、重复的字符串只翻一次")
    print("=" * 78)
    # "Артикул" appears twice in the table; a price list repeats a label in every row, and
    # paying the engine once per occurrence would be both slow and inconsistent.
    asked = engine.seen  # type: ignore[attr-defined]
    print(f"        送进引擎 {len(asked)} 条：{asked}")
    check(asked.count("Артикул") == 1, "重复的字符串只送了一次")
    check(len(asked) == len(set(asked)), "没有任何一条被重复送进引擎")
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
