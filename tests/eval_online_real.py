"""Does the document path work on an online engine, and is it worth it?

The .docx and .xlsx translators were only ever run on the offline model, which is the
weakest thing in the product: it renders 起订量 as "Начало выполнения задачи" and 劳保服 as
"服装". MyMemory needs no key and no payment, so if the document path works through it,
the user has a better option today.

Quota is real (a few thousand characters a day anonymously), so this stays small and uses
the deduplication the spreadsheet translator already does.

Run: python tests/eval_online_real.py
"""
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "online_real.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.docx_io import read_paragraphs, translate_docx  # noqa: E402
from app.xlsx_io import read_cells, translate_xlsx  # noqa: E402

PRICE_LIST = ROOT / "tests" / "fixtures" / "报价单.xlsx"
WORK = ROOT / "tests" / "online_work"
XLSX_OUT = WORK / "报价单_online.xlsx"
DOCX_IN = WORK / "合同.docx"
DOCX_OUT = WORK / "合同_online.docx"

#: What the offline engine produced for the same cells, for side-by-side comparison.
OFFLINE = {
    "A1": "服装商业报价",
    "A3": "产材",
    "B3": "名称",
    "B4": "男人的西装",
    "B5": "金属挑选鞋",
    "B6": "聚PVC涂面棉手套",
    "A8": "价格是一，没有交货计算。",
}

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

CONTRACT = [
    "Наименование: Костюм рабочий мужской",
    "Цена за единицу: 1200 руб.",
    "Минимальная партия: 500 шт.",
    "Срок поставки: 30 календарных дней с момента поступления оплаты.",
    "Оплата производится в рублях по безналичному расчёту.",
    "",
    "Спецодежда поставляется в картонных коробках.",
]

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


def build_docx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{line}</w:t></w:r></w:p>'
        for line in CONTRACT
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W[1:-1]}"><w:body>{body}</w:body></w:document>'
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    import zipfile

    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)

    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = "mymemory"
    cfg.glossary_enabled = True

    print("=" * 78)
    print("把引擎切成联网的 MyMemory（免费、不需要密钥）")
    print("=" * 78)
    from app import engine as engine_mod

    route = engine_mod.build(cfg, cfg.resolved_models_root())
    print(f"  实际选中的引擎：{type(route).__name__}")
    route.load()
    print("  就绪（联网引擎不需要加载模型，所以这一步几乎瞬间完成）")
    print()

    print("=" * 78)
    print("一、Excel 报价单走联网引擎")
    print("=" * 78)
    started = time.perf_counter()
    result = translate_xlsx(cfg, cfg.resolved_models_root(), PRICE_LIST, XLSX_OUT)
    elapsed = time.perf_counter() - started
    print(f"  {result.strings} 条文字（{result.cells} 个单元格），"
          f"用时 {elapsed:.1f} 秒，失败 {len(result.failed)} 处")
    print()
    after = read_cells(XLSX_OUT)
    before = read_cells(PRICE_LIST)
    print("  单元格   原文                                 离线引擎         联网引擎")
    print("  " + "-" * 74)
    for ref in ("A1", "A3", "B3", "B4", "B5", "B6", "A8", "A9"):
        src = before.get(ref, "")
        old = OFFLINE.get(ref, "—")
        print(f"  {ref:<7} {src[:34]:<34} {old[:13]:<15} {after.get(ref, '')}")
    print()
    check(not result.failed, "联网引擎没失败")
    check(bool(after.get("A1")), "标题翻出来了")
    numbers = {r: v for r, v in before.items() if v.replace(".", "").isdigit()}
    check(all(after.get(r) == v for r, v in numbers.items()), "数字单元格一个没动")
    check(set(after) == set(before), "单元格位置不多不少")
    # The point of the exercise: the terms the offline model got wrong.
    better = sum(1 for ref, want in (("A3", "货号"), ("B3", "品名"), ("A8", "单价"))
                 if want in after.get(ref, ""))
    print(f"  离线翻错的 {'货号' if '货号' in after.get('A3', '') else ''}")
    check("产材" not in after.get("A3", ""), f"A3 不再是「产材」：{after.get('A3')!r}")
    print()

    print("=" * 78)
    print("二、Word 合同走联网引擎")
    print("=" * 78)
    build_docx(DOCX_IN)
    docx_result = translate_docx(cfg, cfg.resolved_models_root(), DOCX_IN, DOCX_OUT)
    dst = read_paragraphs(DOCX_OUT)
    print(f"  {docx_result.paragraphs} 段，失败 {len(docx_result.failed)} 处")
    print()
    for src, out in zip(CONTRACT, dst):
        print(f"  原: {src}")
        print(f"  译: {out}")
    print()
    check(not docx_result.failed, "Word 走联网没失败")
    check(len(dst) == len(CONTRACT), f"段数没变（{len(dst)}）")
    check(all(out.strip() for src, out in zip(CONTRACT, dst) if src.strip()),
          "每个非空段都有译文")
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
