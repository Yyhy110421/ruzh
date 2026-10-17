"""End-to-end in the other direction: a Chinese reply through the real engine.

Every measurement so far ran ru->zh, which is the reading half of the job. The writing
half - answering a Russian supplier in Chinese and sending it as Russian - goes the other
way through the same pipeline, and has never been exercised: the 64 zh->ru glossary rules,
Chinese sentence splitting, and the document translators in reverse.

Run: python tests/eval_zh_ru_real.py
"""
import shutil
import sys
import time
import traceback
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "zh_ru_real.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import engine as engine_mod  # noqa: E402
from app.config import AppConfig  # noqa: E402
from app.docx_io import read_paragraphs, translate_docx  # noqa: E402
from app.glossary import Glossary, load_with_builtin  # noqa: E402
from app.segment import rebuild_paragraphs, split_sentences  # noqa: E402
from app.text_tool import translate_text  # noqa: E402

WORK = ROOT / "tests" / "zh_ru_work"
DOCX_IN = WORK / "回复.docx"
DOCX_OUT = WORK / "回复_ru.docx"

#: A reply of the shape this tool exists to produce: short lines, one term per line, and
#: exactly the vocabulary the glossary claims to cover.
REPLY = """您好！
感谢您的询价，现报价如下：
产品名称：男式工作服
单价：1200 卢布，不含运费
起订量：500 件
交货期：收到预付款后 30 个日历日
付款方式：银行转账，预付 30%，余款见提单副本
报价有效期：30 天
如对规格有疑问，请随时联系。
此致敬礼"""

failures = 0


def check(ok: bool, label: str, extra: str = "") -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


def build_docx(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    w = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    body = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{line}</w:t></w:r></w:p>'
        for line in REPLY.split("\n")
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{w[1:-1]}"><w:body>{body}</w:body></w:document>'
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
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)

    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "zh", "ru"
    cfg.translate.engine = "opus"

    print("=" * 78)
    print("中文原文（写给供应商的回复）")
    print("=" * 78)
    for line in REPLY.split("\n"):
        print(f"  | {line}")
    print()

    print("=" * 78)
    print("中文断句")
    print("=" * 78)
    blocks = rebuild_paragraphs(REPLY)
    paragraphs = [b.text for b in blocks if b.kind == "para"]
    print(f"  段落 {len(paragraphs)} 段 / 共 {len(REPLY.splitlines())} 行")
    sentences = [s for p in paragraphs for s in split_sentences(p)]
    for text in sentences[:6]:
        print(f"    · {text}")
    check(len(paragraphs) == len(REPLY.splitlines()),
          f"逐行的空格表没有被硬并成一段（{len(paragraphs)} 段）")
    check(len(sentences) >= len(paragraphs), f"切出 {len(sentences)} 句")
    print()

    print("=" * 78)
    print("翻译（挂上词库，这是产品实际的行为）")
    print("=" * 78)
    table = load_with_builtin(cfg.resolved_glossary_path(), include_builtin=True)
    zh_ru = [r for r in table.rules
             if r.side == "target" and "zh->ru" in (r.pairs or ["zh->ru"])]
    print(f"  词库共 {len(table.rules)} 条，其中 zh->ru 方向 {len(zh_ru)} 条")
    config = AppConfig()
    config.source_lang, config.target_lang = "zh", "ru"
    config.translate.engine = "opus"
    config.glossary_enabled = True
    started = time.perf_counter()
    result = translate_text(config, config.resolved_models_root(), REPLY,
                            mode="paragraph")
    elapsed = time.perf_counter() - started
    print(f"  {result.sentences} 句，用时 {elapsed:.1f} 秒，失败 {len(result.failed)} 处")
    print()
    for line in result.translation.split("\n"):
        print(f"  > {line}")
    print()
    check(not result.failed, "没有失败")
    check(bool(result.translation.strip()), "有译文")
    print()

    print("=" * 78)
    print("词库改动了什么")
    print("=" * 78)
    # The baseline is the same run with the glossary switched off, which is how the
    # product itself turns it off - translate_text loads its own glossary internally.
    config.glossary_enabled = False
    raw = translate_text(config, config.resolved_models_root(), REPLY,
                         mode="paragraph")
    config.glossary_enabled = True
    fired: list[tuple[str, str, str]] = []
    for rule in zh_ru:
        single = Glossary(rules=[rule])
        patched = single.apply_target(raw.translation, "zh", "ru", source=REPLY)
        if patched != raw.translation:
            fired.append((rule.src, rule.dst, rule.when))
    print(f"  {len(fired)} 条规则真的改了东西（对照：本次共 {len(zh_ru)} 条 zh->ru）")
    for src, dst, when in fired[:12]:
        print(f"    {src!r} -> {dst!r}   仅当原文含 {when!r}")
    check(bool(fired), f"zh->ru 词库确实在起作用（{len(fired)} 条命中）")
    print()

    print("=" * 78)
    print("反方向的 Word 翻译")
    print("=" * 78)
    build_docx(DOCX_IN)
    docx_result = translate_docx(cfg, cfg.resolved_models_root(), DOCX_IN, DOCX_OUT)
    before = read_paragraphs(DOCX_IN)
    after = read_paragraphs(DOCX_OUT)
    print(f"  段数 {len(before)} -> {len(after)}，翻译 {docx_result.paragraphs} 段，"
          f"失败 {len(docx_result.failed)} 处")
    for src, dst in list(zip(before, after))[:5]:
        print(f"    {src!r}")
        print(f"      -> {dst!r}")
    check(not docx_result.failed, "Word 反方向没有失败")
    check(len(after) == len(before), "段数没变（每行本来就是独立段落）")
    check(any(after), "译文写回去了")
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
