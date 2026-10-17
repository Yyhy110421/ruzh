"""Translate the text out of a picture.

A contract photographed on a phone, a quotation pasted into WeChat, a scanned price
list: at a trading desk these arrive as images far more often than as documents, and
retyping them is exactly the work this module removes.

Recognition reuses the screen pipeline's engine, so a picture goes through the same
digit repair, space recovery and word repair that a screenshot does. Only the source
differs - a file on disk instead of a rectangle of the screen.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .segment import join_units, rebuild_paragraphs, split_sentences

#: What the file picker offers. Pillow reads all of these.
IMAGE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff")

#: Long edge above which the picture is scaled down before recognition. A twelve
#: megapixel phone photo costs ten seconds or more and finds nothing extra - the
#: printed text on a contract is never that small.
MAX_LONG_EDGE = 3000

@dataclass
class ImageLine:
    """One paragraph, in the picture's reading order, beside its translation."""

    original: str
    translated: str


@dataclass
class ImageResult:
    lines: list[ImageLine] = field(default_factory=list)
    elapsed: float = 0.0
    #: Paragraphs whose translation raised, kept in place as a visible marker.
    failed: int = 0
    ocr_seconds: float = 0.0
    #: Whether the picture was too large and got scaled down first.
    scaled: bool = False
    #: How many identifying things were hidden before the text was sent out.
    redacted: int = 0
    unrestored: list[str] = field(default_factory=list)
    numbers_protected: int = 0
    numbers_unrestored: list[str] = field(default_factory=list)

    @property
    def paragraphs(self) -> int:
        return sum(1 for line in self.lines if line.original.strip())

    @property
    def empty(self) -> bool:
        return self.paragraphs == 0

    @property
    def original_text(self) -> str:
        return "\n\n".join(line.original for line in self.lines if line.original.strip())

    @property
    def translated_text(self) -> str:
        return "\n\n".join(line.translated for line in self.lines if line.translated.strip())


def load_bgr(path: str | Path, max_edge: int = MAX_LONG_EDGE):
    """Open a picture as the BGR array the recogniser takes, scaled down if huge.

    Returns (array, scaled). The array is a contiguous copy: the recogniser hands it to
    OpenCV, which will not accept a negative-stride view of a Pillow buffer.
    """
    from PIL import Image
    import numpy as np

    with Image.open(path) as opened:
        # A multi-page TIFF usually starts with the page that carries the text.
        if getattr(opened, "n_frames", 1) > 1:
            opened.seek(0)
        image = opened.convert("RGB")

    scaled = False
    longest = max(image.size)
    if max_edge and longest > max_edge:
        ratio = max_edge / float(longest)
        size = (max(1, int(image.width * ratio)), max(1, int(image.height * ratio)))
        image = image.resize(size, Image.LANCZOS)
        scaled = True

    return np.asarray(image)[:, :, ::-1].copy(), scaled


def translate_paragraphs(translator, text, on_unit=None, should_stop=None):
    """Split the way the text tab does, translate, and join back.

    Shared by the picture path and the PDF path: both hand over text that arrived with a
    line break at every visual line, and both need it rebuilt into paragraphs first, or
    the sentences come out cut in half.

    Returns (pairs, failures), where pairs is [(original paragraph, translation), ...].
    A sentence that raises keeps its place with the reason in brackets - a gap in the
    middle of a contract is worse than a visible marker.
    """
    pairs: list[tuple[str, str]] = []
    failures = 0
    for block in rebuild_paragraphs(text):
        if should_stop is not None and should_stop():
            break
        pieces: list[str] = []
        for unit in split_sentences(block.text):
            try:
                pieces.append(translator(unit))
            except Exception as exc:  # noqa: BLE001
                failures += 1
                pieces.append("[" + str(exc) + "]")
            if on_unit is not None:
                on_unit()
        pairs.append((block.text, join_units(pieces) if pieces else ""))
    return pairs, failures


def translate_image(
    cfg,
    models_root: str,
    src_path: str | Path,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    translator: Callable[[str], str] | None = None,
    ocr=None,
) -> ImageResult:
    """Recognise a picture and translate what it says, paragraph by paragraph.

    Pass translator and/or ocr to run without loading models; the tests do. A
    caller-supplied translator reports no masking counts, which is the honest answer:
    it never went through the redactor.
    """
    from .ocr_engine import OcrEngine, script_for

    started = time.perf_counter()
    result = ImageResult()

    if ocr is None:
        engine = OcrEngine(cfg.ocr, models_root=cfg.resolved_models_root(),
                           script=script_for(cfg.source_lang))
        engine.load()
        ocr = engine

    frame, result.scaled = load_bgr(src_path)
    recognised = ocr.recognize(frame)
    result.ocr_seconds = recognised.elapsed

    if recognised.empty:
        result.elapsed = time.perf_counter() - started
        return result

    # Screen lines joined by a newline, then the same paragraph rebuild the text tab
    # uses: a contract photographed on a phone wraps every visual line, and translating
    # those one at a time cuts the sentences in half.
    raw = "\n".join(line.text for line in recognised.lines)

    if translator is None:
        from .text_tool import Translator

        translator = Translator(cfg, models_root)

    total = sum(len(split_sentences(block.text)) for block in rebuild_paragraphs(raw))
    done = [0]

    def one_unit() -> None:
        done[0] += 1
        if on_progress is not None:
            on_progress(done[0], total)

    pairs, failed = translate_paragraphs(translator, raw, on_unit=one_unit,
                                         should_stop=should_stop)
    result.failed = failed
    for original, translated in pairs:
        result.lines.append(ImageLine(original=original, translated=translated))

    # Counts only exist on a translator we built ourselves; a stub reports zero, which
    # is what actually happened.
    result.redacted = int(getattr(translator, "redacted_count", 0) or 0)
    result.unrestored = list(getattr(translator, "unrestored", []) or [])
    result.numbers_protected = int(getattr(translator, "numbers_protected", 0) or 0)
    result.numbers_unrestored = list(getattr(translator, "numbers_unrestored", []) or [])
    result.elapsed = time.perf_counter() - started
    return result
