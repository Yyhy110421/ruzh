"""Translate a PDF: a contract, a quotation, a signed scan.

Contracts arrive as PDF and nothing else. A price list exported from the supplier's
accounting system is a PDF, a signed page photographed and mailed is a PDF, a scan of
last year's agreement is a PDF. The other document formats in this program are a
convenience; this one is the job.

Two kinds of page turn up, and they want opposite treatment:

* a digital page carries its own text, so reading it is exact and takes milliseconds
* a scanned page is a picture, so the text has to be recognised - the same engine the
  screen tab uses, with the same number protection and glossary

and it is decided per page, not per document. A contract that was signed, scanned and
then emailed has a digital first page and photographed ones after it.

The PDF library is pypdfium2 (BSD-3-Clause / Apache-2.0), not PyMuPDF, which is AGPL-3.0:
shipping that in a distribution obliges the whole distribution, and the commercial licence
it offers is sold by Artifex rather than by us. pypdfium2 covers both halves of what is
needed - read a text layer, and render a page to an image.
"""

from __future__ import annotations

import io
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .image_tool import translate_image, translate_paragraphs

#: What the file picker offers.
PDF_SUFFIXES = (".pdf",)

#: A page holding less text than this is treated as a scan. A digital page carries at
#: least a paragraph; a scanned one carries nothing, or a scrap of a header left behind
#: when somebody ran OCR on it before putting it in the file.
SCANNED_THRESHOLD = 40

#: Resolution a scanned page is rendered at before recognition. 200 dpi is what a flatbed
#: scanner produces, and it is what the recogniser's accuracy was measured on. Higher
#: costs time without finding anything the eye cannot see.
RENDER_DPI = 200


class PdfError(Exception):
    """The file could not be opened as a PDF, or is not one we can read."""


@dataclass
class PdfPage:
    """One page, as it was read and as it came out."""

    number: int
    original: str
    translated: str
    #: True when the page was a picture and had to be recognised.
    from_ocr: bool = False


@dataclass
class PdfResult:
    pages: list[PdfPage] = field(default_factory=list)
    elapsed: float = 0.0
    #: Sentences whose translation raised, kept in place as a visible marker.
    failed: int = 0
    scanned_pages: int = 0
    digital_pages: int = 0
    #: How many identifying things were hidden before the text was sent out.
    redacted: int = 0
    unrestored: list[str] = field(default_factory=list)
    numbers_protected: int = 0
    numbers_unrestored: list[str] = field(default_factory=list)

    @property
    def total_pages(self) -> int:
        return len(self.pages)

    @property
    def empty(self) -> bool:
        return not any(page.original.strip() for page in self.pages)

    @property
    def original_text(self) -> str:
        return "\n\n".join(
            f"―― 第 {page.number} 页 ――\n{page.original.strip()}"
            for page in self.pages if page.original.strip())

    @property
    def translated_text(self) -> str:
        return "\n\n".join(
            f"―― 第 {page.number} 页 ――\n{page.translated.strip()}"
            for page in self.pages if page.translated.strip())


def open_document(path: str | Path):
    """Open a PDF, with the errors a customer can actually cause spelled out."""
    import pypdfium2

    source = Path(path)
    if not source.is_file():
        raise PdfError("找不到这个文件：" + str(source))
    try:
        document = pypdfium2.PdfDocument(str(source))
    except Exception as exc:  # noqa: BLE001
        detail = str(exc)
        lowered = detail.lower()
        if "password" in lowered or "encrypted" in lowered:
            raise PdfError("这个 PDF 有密码，先解开再翻") from exc
        raise PdfError("打不开这个 PDF：" + detail) from exc
    if len(document) == 0:
        document.close()
        raise PdfError("这个 PDF 一页都没有")
    return document


#: The whitespace a PDF's text layer actually emits. It separates words with a
#: non-breaking space, and that is not a space to a regular expression, to the word list,
#: or to the number masker: "600 000" arrives as "600\xa0000" and stops being a number,
#: which is exactly the loss app/numbers.py exists to prevent.
_EXOTIC_SPACE = (
    "\u00a0",      # no-break space - the common one
    "\u2007",      # figure space
    "\u202f",      # narrow no-break space
    "\u2009",      # thin space
    "\u200b",      # zero-width space
    "\ufeff",      # byte-order mark that survived a bad conversion
    "\t",
    "\r",
)


def page_text(page) -> str:
    """The text layer of one page, with real spaces, or "" when there is none."""
    textpage = None
    try:
        textpage = page.get_textpage()
        raw = textpage.get_text_range() or ""
    except Exception:  # noqa: BLE001
        # A single damaged page should not lose the other twenty.
        return ""
    finally:
        if textpage is not None:
            try:
                textpage.close()
            except Exception:  # noqa: BLE001
                pass
    for char in _EXOTIC_SPACE:
        raw = raw.replace(char, " ")
    return raw.strip()


def render_page(page, dpi: int = RENDER_DPI) -> bytes:
    """One page as PNG bytes, for the recogniser."""
    bitmap = page.render(scale=dpi / 72.0)
    try:
        buffer = io.BytesIO()
        bitmap.to_pil().save(buffer, format="PNG")
        return buffer.getvalue()
    finally:
        try:
            bitmap.close()
        except Exception:  # noqa: BLE001
            pass


def translate_pdf(
    cfg,
    models_root: str,
    src_path: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    translator: Callable[[str], str] | None = None,
    ocr=None,
    dpi: int = RENDER_DPI,
) -> PdfResult:
    """Translate every page of a PDF, digital or scanned.

    Pass translator and/or ocr to run without loading models; the tests do. A
    caller-supplied translator reports no masking counts, which is the honest answer:
    it never went through the redactor.
    """
    from .ocr_engine import OcrEngine, script_for

    started = time.perf_counter()
    result = PdfResult()
    document = open_document(src_path)

    try:
        total = len(document)
        if translator is None:
            from .text_tool import Translator

            translator = Translator(cfg, models_root)

        with tempfile.TemporaryDirectory(prefix="pdf-pages-") as scratch:
            scratch_path = Path(scratch)
            for index in range(total):
                if should_stop is not None and should_stop():
                    break
                # Read and render first, then close the page: pdfium holds the decoded
                # page until it is closed, and recognising one image takes long enough
                # that a few dozen pages would otherwise pile up in memory.
                page = document[index]
                try:
                    text = page_text(page)
                    shot = scratch_path / f"page-{index + 1:03d}.png"
                    scanned = len(text) < SCANNED_THRESHOLD
                    if scanned:
                        shot.write_bytes(render_page(page, dpi))
                finally:
                    try:
                        page.close()
                    except Exception:  # noqa: BLE001
                        pass

                translated = False
                if not scanned:
                    result.digital_pages += 1
                    pairs, failed = translate_paragraphs(
                        translator, text, should_stop=should_stop)
                    result.failed += failed
                    result.pages.append(PdfPage(
                        number=index + 1,
                        original=text,
                        translated="\n\n".join(pair[1] for pair in pairs if pair[1]),
                        from_ocr=False))
                    translated = True

                if scanned:
                    # Only now is the recogniser needed, so a digital PDF never pays for
                    # loading it - which is several seconds before the first page.
                    if ocr is None:
                        engine = OcrEngine(cfg.ocr,
                                           models_root=cfg.resolved_models_root(),
                                           script=script_for(cfg.source_lang))
                        engine.load()
                        ocr = engine
                    seen = translate_image(cfg, models_root, shot, ocr=ocr,
                                           translator=translator,
                                           should_stop=should_stop)
                    # Whichever found more, won. A short digital page - a signature
                    # block, a page that is mostly a stamp - recognises as a few
                    # mangled letters, and its own text layer is exact and longer. A
                    # scan with a stray header in the text layer is the other way
                    # round: the header is a line, the recognised page is the whole
                    # page. Comparing lengths settles both without another setting.
                    if text and len(text) >= len(seen.original_text):
                        result.digital_pages += 1
                        pairs, failed = translate_paragraphs(
                            translator, text, should_stop=should_stop)
                        result.failed += failed
                        result.pages.append(PdfPage(
                            number=index + 1,
                            original=text,
                            translated="\n\n".join(pair[1] for pair in pairs if pair[1]),
                            from_ocr=False))
                    else:
                        result.scanned_pages += 1
                        result.failed += seen.failed
                        result.pages.append(PdfPage(
                            number=index + 1,
                            original=seen.original_text,
                            translated=seen.translated_text,
                            from_ocr=True))
                if on_progress is not None:
                    on_progress(index + 1, total)
    finally:
        document.close()

    result.redacted = int(getattr(translator, "redacted_count", 0) or 0)
    result.unrestored = list(getattr(translator, "unrestored", []) or [])
    result.numbers_protected = int(getattr(translator, "numbers_protected", 0) or 0)
    result.numbers_unrestored = list(getattr(translator, "numbers_unrestored", []) or [])
    result.elapsed = time.perf_counter() - started
    return result
