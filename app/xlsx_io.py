"""Translate a spreadsheet in place, keeping every cell's formatting and formulas.

Trade runs on price lists: a supplier's quotation arrives as an .xlsx, and retyping it is
the same wasted afternoon as retyping a contract. So the same treatment as the Word
translator, on the same principle - copy the archive, rewrite only the text, and touch
nothing else. Styles, column widths, merged cells, number formats, formulas and charts are
all copied across byte for byte.

Two string layouts exist in the wild and both are common:

* Excel itself writes a **shared string table** in xl/sharedStrings.xml, and each cell
  holds an index into it (t="s").
* openpyxl and several other writers put the text **inline** in the cell instead
  (t="inlineStr").

Handling only one of them would mean half the files that arrive come back untouched, so
both are collected and rewritten.

Cells that hold numbers, dates or formulas are never touched: their text is not text, and
translating a number would corrupt the sheet. String cells whose content is a formula
result (t="str") are left alone for the same reason.
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
from .segment import join_units, split_sentences
from .text_tool import Translator
from .translator import TranslateError

#: The shared string table, when the writer used one.
SHARED_STRINGS = "xl/sharedStrings.xml"
#: Every worksheet is a part of its own.
SHEET_DIR = "xl/worksheets/"
SHEET_SUFFIX = ".xml"

_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
#: One entry of the shared string table.
_S_SI = _S + "si"
#: An inline string attached to a single cell.
_S_IS = _S + "is"
_S_T = _S + "t"
_S_C = _S + "c"
_S_V = _S + "v"


@dataclass
class XlsxResult:
    """What the run did, in the same shape as the other translators for the UI."""

    strings: int = 0        #: distinct strings that came back translated
    cells: int = 0          #: cells whose text was replaced
    failed: list[int] = field(default_factory=list)
    elapsed: float = 0.0
    output: str = ""
    #: Identifying things hidden before the text went to an online engine, and any
    #: placeholder that never came back. The second list has to reach the user.
    redacted: int = 0
    unrestored: list[str] = field(default_factory=list)


def _text_parts(names) -> list[str]:
    """The parts that can carry translatable text."""
    return [name for name in names
            if name == SHARED_STRINGS
            or (name.startswith(SHEET_DIR) and name.endswith(SHEET_SUFFIX))]


def _string_holders(root) -> list:
    """Every string carrier in one part, in document order."""
    return [element for element in root.iter() if element.tag in (_S_SI, _S_IS)]


def _holder_text(holder) -> str:
    """A carrier's text, with rich-text runs concatenated."""
    return "".join(node.text or "" for node in holder.iter(_S_T))


def _write_holder(holder, value: str) -> bool:
    """Put the translation into a carrier, dropping any further text nodes.

    A translated string is one string, so a second text node left behind would reappear in
    the middle of it.
    """
    nodes = list(holder.iter(_S_T))
    if not nodes:
        if not value:
            return False
        node = ET.SubElement(holder, _S_T)
        node.text = value
        node.set(ooxml.XML_SPACE, "preserve")
        return True
    nodes[0].text = value
    nodes[0].set(ooxml.XML_SPACE, "preserve")
    for node in nodes[1:]:
        node.text = ""
    return True


def read_strings(path: str | Path) -> list[str]:
    """Every string in the workbook, in part order. For inspection and tests."""
    out: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for name in _text_parts(archive.namelist()):
            raw = archive.read(name)
            ooxml.register_namespaces(raw)
            root = ET.fromstring(raw)
            out.extend(_holder_text(holder) for holder in _string_holders(root))
    return out


def read_cells(path: str | Path) -> dict[str, str]:
    """Every filled cell's text by reference, resolving both string layouts.

    Numbers come back as the raw stored value, because that is what they are; this shows
    what a workbook holds rather than reading it as data. Shared strings are resolved
    through the table, so a cell whose text lives there reads the same as an inline one.
    """
    out: dict[str, str] = {}
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        shared: list[str] = []
        if SHARED_STRINGS in names:
            raw = archive.read(SHARED_STRINGS)
            ooxml.register_namespaces(raw)
            shared = [_holder_text(holder)
                      for holder in ET.fromstring(raw).iter(_S_SI)]
        for name in names:
            if not (name.startswith(SHEET_DIR) and name.endswith(SHEET_SUFFIX)):
                continue
            raw = archive.read(name)
            ooxml.register_namespaces(raw)
            for cell in ET.fromstring(raw).iter(_S_C):
                ref = cell.get("r")
                if not ref:
                    continue
                kind = cell.get("t")
                if kind == "inlineStr":
                    out[ref] = _holder_text(cell)
                    continue
                node = cell.find(_S_V)
                if node is None:
                    continue
                if kind == "s" and (node.text or "").strip():
                    index = int(node.text)
                    out[ref] = shared[index] if 0 <= index < len(shared) else ""
                else:
                    out[ref] = node.text or ""
    return out


def translate_xlsx(
    cfg: AppConfig,
    models_root: str,
    src_path: str | Path,
    dst_path: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    translator: Callable[[str], str] | None = None,
) -> XlsxResult:
    """Write a translated copy of a spreadsheet, keeping everything but the text.

    Pass a translator to run without loading a model (the tests do).
    """
    started = time.perf_counter()
    source = Path(src_path)
    target = Path(dst_path)

    with zipfile.ZipFile(source) as archive:
        parts = []
        for name in _text_parts(archive.namelist()):
            raw = archive.read(name)
            ooxml.register_namespaces(raw)
            parts.append((name, raw, ET.fromstring(raw)))

    entries: list[tuple[object, str]] = []
    for _, _, root in parts:
        for holder in _string_holders(root):
            entries.append((holder, _holder_text(holder)))

    # Spreadsheets repeat themselves: one label sits in dozens of cells. Translating every
    # occurrence would be slow, and worse, could render the same term two different ways in
    # one document. Each distinct string is translated once and every cell that used it
    # gets that answer.
    distinct: list[str] = []
    seen: set[str] = set()
    for _, text in entries:
        if text.strip() and text not in seen:
            seen.add(text)
            distinct.append(text)

    plan: list[tuple[str, str]] = []
    for text in distinct:
        for sentence in split_sentences(text):
            plan.append((text, sentence))

    engine = translator if translator is not None else Translator(cfg, models_root)
    # Kept separately so the run can report what was masked. A caller-supplied callable has
    # none of that and reports nothing, which is the honest answer.
    unit = engine
    rendered: dict[str, list[str]] = {}
    failed: list[int] = []

    for done, (owner, sentence) in enumerate(plan, start=1):
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
        rendered.setdefault(owner, []).append(piece)
        if on_progress is not None:
            on_progress(done, len(plan))

    translated = {text: join_units(pieces) for text, pieces in rendered.items()}

    cells = 0
    for holder, text in entries:
        value = translated.get(text)
        if value and _write_holder(holder, value):
            cells += 1

    ooxml.rewrite_zip(
        source, target,
        {name: ooxml.rebuild_part(raw, root) for name, raw, root in parts},
    )

    return XlsxResult(
        redacted=getattr(unit, "redacted_count", 0),
        unrestored=list(getattr(unit, "unrestored", [])),
        strings=len(translated),
        cells=cells,
        failed=failed,
        elapsed=time.perf_counter() - started,
        output=str(target),
    )
