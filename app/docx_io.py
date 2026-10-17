"""Translate a Word document in place, keeping its layout.

Trade documents arrive as .docx: a contract, a quotation, a specification. Retyping them
into the text tab is exactly the work the user is trying to avoid, and pasting them in
loses the tables and the numbering they have to reply against.

There is deliberately no python-docx dependency. A .docx is a zip of XML parts and the only
part this module rewrites is word/document.xml; everything else - styles, numbering, images,
headers, the theme - is copied across byte for byte, so the translated file opens looking
like the original.

The rewrite is paragraph by paragraph, not run by run. One sentence is routinely split
across several runs, because Word starts a new run on a spell-check boundary, a language
change or a formatting change; translating run by run would cut the sentence in half again,
which is the very thing app/segment.py exists to avoid. The translated paragraph goes into
the first text run and the remaining runs of that paragraph are emptied, so the paragraph
keeps its style, alignment and indentation while inline emphasis inside it does not survive.
That is the honest trade: layout that holds, per-word bold that does not.

The XML is reparsed rather than edited with a hand-written scanner, but the original root
start tag is spliced back in afterwards. ElementTree only writes the namespace declarations
it actually uses, and Word writes prefixes such as w14 into mc:Ignorable without ever using
them as element names - dropping their declarations would leave the attribute referencing a
prefix that is no longer declared, which Word reports as unreadable content.
"""

from __future__ import annotations

import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from . import ooxml
from .config import AppConfig
from .segment import continues_previous, join_units, split_sentences
from .text_tool import Translator
from .translator import TranslateError

#: The only part that carries the visible text of the body.
DOCUMENT_PART = "word/document.xml"

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_W_P = _W + "p"
_W_R = _W + "r"
_W_T = _W + "t"
_W_TAB = _W + "tab"
_W_BR = _W + "br"
_W_CR = _W + "cr"


@dataclass
class DocxResult:
    """What the run did, in the same shape as a text translation for the UI."""

    paragraphs: int = 0
    sentences: int = 0
    failed: list[int] = field(default_factory=list)
    elapsed: float = 0.0
    output: str = ""
    #: Identifying things hidden before the text went to an online engine, and any
    #: placeholder that never came back. The second list has to reach the user.
    redacted: int = 0
    unrestored: list[str] = field(default_factory=list)


def _runs(paragraph) -> list:
    """The paragraph's runs, in document order, including runs inside hyperlinks."""
    return list(paragraph.iter(_W_R))


def _paragraph_text(paragraph) -> str:
    """The paragraph's text as the translator should see it.

    A tab or a line break inside a paragraph is a visual separator, not content, and it
    cannot survive sentence-level translation. Both are read as a plain space, which is
    what the engine expects between two words anyway.
    """
    parts = []
    for run in _runs(paragraph):
        for child in run:
            if child.tag == _W_T:
                parts.append(child.text or "")
            elif child.tag in (_W_TAB, _W_BR, _W_CR):
                parts.append(" ")
    return "".join(parts)


def read_paragraphs(path: str | Path) -> list[str]:
    """Every paragraph's text, in document order, including those inside tables."""
    with zipfile.ZipFile(path) as archive:
        raw = archive.read(DOCUMENT_PART)
    root = ET.fromstring(raw)
    return [_paragraph_text(paragraph) for paragraph in root.iter(_W_P)]


def translate_docx(
    cfg: AppConfig,
    models_root: str,
    src_path: str | Path,
    dst_path: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    translator: Callable[[str], str] | None = None,
) -> DocxResult:
    """Write a translated copy of a .docx, keeping everything but the body text.

    Pass a translator to run without loading a model (the tests do).
    """
    started = time.perf_counter()
    source = Path(src_path)
    target = Path(dst_path)

    with zipfile.ZipFile(source) as archive:
        raw = archive.read(DOCUMENT_PART)

    ooxml.register_namespaces(raw)
    root = ET.fromstring(raw)

    paragraphs = list(root.iter(_W_P))
    texts = [_paragraph_text(paragraph) for paragraph in paragraphs]
    parents = ooxml.element_parents(root)

    # Consecutive paragraphs that are only a wrapped continuation of each other become one
    # unit, and collapse into one paragraph on the way out. Text pasted out of a PDF
    # routinely lands in Word as one paragraph per visual line, and translating those
    # separately cuts the sentences in half - the same failure app/segment.py exists to
    # prevent in the text tab.
    #
    # Only paragraphs with the same parent are ever merged, so two table cells can never
    # be run together.
    groups: list[list[int]] = []
    for index, text in enumerate(texts):
        if not text.strip():
            groups.append([index])
            continue
        joined = False
        if groups and texts[groups[-1][-1]].strip():
            last = groups[-1][-1]
            same_parent = parents.get(id(paragraphs[last])) is parents.get(id(paragraphs[index]))
            joined = same_parent and continues_previous(texts[last], text)
        if joined:
            groups[-1].append(index)
        else:
            groups.append([index])

    # One request per sentence of every group that carries text at all.
    plan: list[tuple[int, str]] = []
    for group_index, group in enumerate(groups):
        if not texts[group[0]].strip():
            continue
        merged = join_units([texts[index] for index in group])
        for sentence in split_sentences(merged):
            plan.append((group_index, sentence))

    engine = translator if translator is not None else Translator(cfg, models_root)
    # Kept separately so the run can report what was masked. A caller-supplied callable has
    # none of that and reports nothing, which is the honest answer.
    unit = engine
    rendered: dict[int, list[str]] = {}
    failed: list[int] = []

    for done, (index, sentence) in enumerate(plan, start=1):
        if should_stop is not None and should_stop():
            break
        try:
            piece = engine(sentence)
        except TranslateError as exc:
            failed.append(done)
            piece = f"[第 {done} 句翻译失败：{exc}]"
        except Exception as exc:  # noqa: BLE001
            failed.append(done)
            piece = f"[第 {done} 句翻译失败：{exc}]"
        rendered.setdefault(index, []).append(piece)
        if on_progress is not None:
            on_progress(done, len(plan))

    translated_groups = 0
    for group_index, group in enumerate(groups):
        if group_index not in rendered:
            continue
        translated_groups += 1
        paragraph = paragraphs[group[0]]
        runs = _runs(paragraph)
        target_run = None
        for run in runs:
            if any(child.tag == _W_T for child in run):
                target_run = run
                break
        if target_run is None:
            continue
        # The translated paragraph is one string, so every text element outside the run it
        # goes into has to go: leaving a tab or a break behind would place it in the middle
        # of the new sentence.
        for run in runs:
            if run is target_run:
                continue
            for child in list(run):
                if child.tag in (_W_T, _W_TAB, _W_BR, _W_CR):
                    run.remove(child)
        written = False
        for child in list(target_run):
            if child.tag != _W_T:
                continue
            child.text = join_units(rendered[group_index]) if not written else ""
            child.set(ooxml.XML_SPACE, "preserve")
            written = True
        # The rest of the group has been folded into this paragraph, so leaving them would
        # repeat the layout they came from with nothing in it. They share this paragraph's
        # parent by construction, and the head stays, so no container is emptied.
        for index in group[1:]:
            parent = parents.get(id(paragraphs[index]))
            if parent is not None:
                parent.remove(paragraphs[index])

    ooxml.rewrite_zip(source, target, {DOCUMENT_PART: ooxml.rebuild_part(raw, root)})

    return DocxResult(
        redacted=getattr(unit, "redacted_count", 0),
        unrestored=list(getattr(unit, "unrestored", [])),
        paragraphs=translated_groups,
        sentences=len(plan),
        failed=failed,
        elapsed=time.perf_counter() - started,
        output=str(target),
    )
