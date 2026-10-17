"""Does a Word document survive translation with its layout intact?

The point of translating a .docx in place is that the contract still looks like a contract
afterwards: same styles, same tables, same everything except the words. So the checks here
are mostly about what must NOT change, plus the two things that must: the text is replaced,
and a sentence split across several runs comes back as one sentence rather than two halves.

The translator is injected, so this runs in seconds and does not need a model.
"""
import shutil
import sys
import traceback
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "docx.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.docx_io import read_paragraphs, translate_docx  # noqa: E402

WORK = ROOT / "tests" / "docx_work"
SOURCE = WORK / "contract.docx"
OUTPUT = WORK / "contract.zh.docx"

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>"""

RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

STYLES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:styleId="Normal"><w:name w:val="Normal"/></w:style>
</w:styles>"""

#: Word's own namespace soup, including the prefixes it lists in mc:Ignorable without ever
#: using them as element names. Dropping those declarations is what makes Word refuse a file.
DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:w14="http://schemas.microsoft.com/office/word/2010/wordml" mc:Ignorable="w14">
<w:body>
<w:p><w:pPr><w:pStyle w:val="Normal"/></w:pPr><w:r><w:t>Срок поставки — </w:t></w:r><w:r><w:t>30 календарных</w:t></w:r><w:r><w:t xml:space="preserve"> дней.</w:t></w:r></w:p>
<w:p/>
<w:p><w:r><w:t>Цена</w:t></w:r><w:r><w:tab/></w:r><w:r><w:t>1 200 руб.</w:t></w:r></w:p>
<w:tbl><w:tr><w:tc><w:p><w:r><w:t>Артикул</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>Количество</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
<w:sectPr/>
</w:body>
</w:document>"""

PARTS = {
    "[Content_Types].xml": CONTENT_TYPES,
    "_rels/.rels": RELS,
    "word/document.xml": DOCUMENT,
    "word/styles.xml": STYLES,
}

EXPECTED_BEFORE = [
    "Срок поставки — 30 календарных дней.",
    "",
    "Цена 1 200 руб.",
    "Артикул",
    "Количество",
]

EXPECTED_AFTER = [
    "【Срок поставки — 30 календарных дней.】",
    "",
    "【Цена 1 200 руб.】",
    "【Артикул】",
    "【Количество】",
]

failures = 0


def check(condition: bool, label: str, extra: str = "") -> None:
    global failures
    if condition:
        print(f"  [OK  ] {label}")
    else:
        failures += 1
        print(f"  [FAIL] {label}")
        if extra:
            print(f"         {extra}")


def fake(unit: str) -> str:
    return f"【{unit}】"


def build() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    with zipfile.ZipFile(SOURCE, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, text in PARTS.items():
            archive.writestr(name, text)


def part_bytes(path: Path, name: str) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read(name)


def main() -> int:
    global failures
    build()
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"

    print("=" * 78)
    print("一、读出来的段落（跨 run 的句子要拼回一句，表格里的也算）")
    print("=" * 78)
    before = read_paragraphs(SOURCE)
    ok = before == EXPECTED_BEFORE
    check(ok, f"读出 {len(before)} 段", "" if ok else f"实际：{before}")
    print()

    print("=" * 78)
    print("二、翻译并写出")
    print("=" * 78)
    result = translate_docx(cfg, "", SOURCE, OUTPUT, translator=fake)
    check(result.paragraphs == 4, f"翻译了 {result.paragraphs} 段（空段落跳过）")
    check(result.sentences == 4, f"送进引擎 {result.sentences} 句")
    check(not result.failed, "没有失败")
    after = read_paragraphs(OUTPUT)
    ok = after == EXPECTED_AFTER
    check(ok, "译文写回去了", "" if ok else f"实际：{after}")
    print()

    print("=" * 78)
    print("三、除正文外，其它部分必须一个字节都没变")
    print("=" * 78)
    for name in PARTS:
        if name == "word/document.xml":
            continue
        same = part_bytes(SOURCE, name) == part_bytes(OUTPUT, name)
        check(same, f"{name} 原样")
    print()

    print("=" * 78)
    print("四、Word 的命名空间声明必须保住（丢了 Word 会说文件损坏）")
    print("=" * 78)
    document = part_bytes(OUTPUT, "word/document.xml")
    for prefix, uri in (
        ("mc", "http://schemas.openxmlformats.org/markup-compatibility/2006"),
        ("w14", "http://schemas.microsoft.com/office/word/2010/wordml"),
        ("r", "http://schemas.openxmlformats.org/officeDocument/2006/relationships"),
    ):
        declared = f'xmlns:{prefix}="{uri}"'.encode()
        check(declared in document, f"xmlns:{prefix} 还在")
    check(b'mc:Ignorable="w14"' in document, "mc:Ignorable 还在")
    try:
        ET.fromstring(document)
        check(True, "输出是合法 XML（没有未声明的前缀）")
    except ET.ParseError as exc:
        check(False, "输出是合法 XML", str(exc))
    print()

    print("=" * 78)
    print("五、跨 run 的那一段：run 结构保留，但译文只落在一处")
    print("=" * 78)
    root = ET.fromstring(document)
    paragraphs = list(root.iter(W + "p"))
    first = paragraphs[0]
    # The runs stay where they were - only the text nodes outside the first one are
    # dropped. A translated paragraph is one string, so leaving a stray text node in a
    # later run would place it in the middle of the new sentence.
    runs = list(first.iter(W + "r"))
    texts = [node.text or "" for node in first.iter(W + "t")]
    non_empty = [text for text in texts if text]
    check(len(runs) == 3, f"run 还在（{len(runs)} 个）")
    check(len(non_empty) == 1, f"只有 1 处文本节点有字：{non_empty}")
    check(non_empty == ["【Срок поставки — 30 календарных дней.】"],
          "而且整句都在里面")
    styles = [node.get(W + "val") for node in first.iter(W + "pStyle")]
    check(styles == ["Normal"], f"段落样式保住了：{styles}")
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
