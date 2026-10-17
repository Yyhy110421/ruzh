"""End-to-end: a real Russian contract .docx through the real offline engine.

The structural test uses a fake translator so it can run in seconds; this one uses the
engine, because the point of translating a document is the text that comes out. It also
shows the paragraph rebuild doing its job inside Word, where the same hard-wrapping
problem exists.
"""
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "docx_real.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.docx_io import read_paragraphs, translate_docx  # noqa: E402

WORK = ROOT / "tests" / "docx_real_work"
SOURCE = WORK / "合同.docx"
OUTPUT = WORK / "合同_译文.docx"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

#: A contract as it really arrives: the body text is hard-wrapped at a column that has
#: nothing to do with where the sentences end, and there is a price table underneath.
BODY = [
    "Срок поставки — 30 календарных дней с момента",
    "поступления оплаты на наш расчётный счёт.",
    "",
    "Цена указана за одну тонну без учёта доставки.",
    "Оплата производится в рублях.",
]

TABLE = [("Артикул", "Количество"), ("СП-001", "500")]


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def para(text: str) -> str:
    return f'<w:p><w:r><w:t xml:space="preserve">{esc(text)}</w:t></w:r></w:p>'


def build() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    rows = "".join(
        "<w:tr>" + "".join(f"<w:tc>{para(cell)}</w:tc>" for cell in row) + "</w:tr>"
        for row in TABLE
    )
    body = "".join(para(line) for line in BODY)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{body}<w:tbl>{rows}</w:tbl></w:body></w:document>"
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
    with zipfile.ZipFile(SOURCE, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def main() -> int:
    build()
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = "opus"
    cfg.glossary_enabled = True

    print("=" * 78)
    print("输入（Word 里的样子）")
    print("=" * 78)
    for line in BODY:
        print(f"  | {line}")
    print()

    print("=" * 78)
    print("用离线引擎翻译整份文档…")
    print("=" * 78)
    started = time.perf_counter()
    result = translate_docx(cfg, cfg.resolved_models_root(), SOURCE, OUTPUT)
    print(f"  {result.paragraphs} 段 / {result.sentences} 句，"
          f"用时 {time.perf_counter() - started:.1f} 秒，失败 {len(result.failed)} 处")
    print()

    before = read_paragraphs(SOURCE)
    after = read_paragraphs(OUTPUT)

    print("=" * 78)
    print("输入文档的段落")
    print("=" * 78)
    for index, text in enumerate(before, start=1):
        print(f"  {index:>2}. {text!r}")
    print()

    print("=" * 78)
    print("输出文档的段落")
    print("=" * 78)
    for index, text in enumerate(after, start=1):
        print(f"  {index:>2}. {text!r}")
    print()

    failures = 0

    def check(condition: bool, label: str) -> None:
        nonlocal failures
        if not condition:
            failures += 1
        print(f"  [{'OK  ' if condition else 'FAIL'}] {label}")

    print("=" * 78)
    print("检查")
    print("=" * 78)
    # The two halves of the wrapped sentence arrive as two paragraphs and have to come back
    # as one. That is why the output legitimately has fewer paragraphs than the input.
    check(len(after) < len(before),
          f"跨段落的半句话被合并了（{len(before)} 段 -> {len(after)} 段）")
    merged = [text for text in after if "收到付款" in text and "30" in text]
    check(len(merged) == 1, f"合并后是一句完整的话：{merged[:1]}")
    check("" in after, "空行（段间分隔）还在")
    check(any("数量" in text for text in after), "表格里的单元格也翻了")
    leftovers = [text for text in after if text.strip() and text in before]
    check(not leftovers, f"没有原样照抄的段落：{leftovers}")
    print()

    print("=" * 78)
    print(f"失败 {failures} 项")
    print(f"输出文件: {OUTPUT}  ({OUTPUT.stat().st_size} 字节)")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
