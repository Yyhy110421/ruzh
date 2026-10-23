"""Translate a block of text, or a text file, with the same engines as screen mode.

Screen translation is the eye-catching half; the thing a trade user actually needs is
to paste an email, a quotation or a contract clause and get it translated. That is the
same pipeline minus capture and OCR, so this module reuses the engine registry and the
term glossary rather than duplicating any translation logic.

Two shapes of input, and they need opposite treatment:

* A subtitle file or a spreadsheet export keeps **one record per line**. The line break is
  the structure, so those are translated line by line.
* An email or a contract paragraph is **hard-wrapped** by the client it was copied from.
  Translating that line by line feeds the engine half a sentence per request - see
  app/segment.py - so it is rebuilt into paragraphs first and then translated one
  **sentence** at a time.

Sending a whole document as one request is not the answer either: it would exceed the
online services' per-request limits, and one failure would lose the lot.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .config import AppConfig

from .glossary import Glossary, GlossaryError, load_with_builtin
from . import numbers
from .redact import Redactor
from .segment import join_units, rebuild_paragraphs, split_sentences
from .translator import TranslateError, finalise


@dataclass
class TextResult:
    source: str
    translation: str
    #: Paragraphs translated in paragraph mode, lines translated in line mode.
    lines: int = 0
    #: Requests actually sent to the engine (sentences, or lines in line mode).
    sentences: int = 0
    mode: str = "line"
    failed: list[int] = field(default_factory=list)
    elapsed: float = 0.0
    #: How many identifying things were hidden before the text was sent out.
    redacted: int = 0
    #: Placeholders an engine never gave back, if any. They have to be shown: a
    #: translation with [[C1]] left in the middle of it is worse than no masking.
    unrestored: list[str] = field(default_factory=list)
    #: Numbers taken out of the translator's reach, and any it swallowed. A missing
    #: quantity in a quotation is not something the reader can notice for themselves.
    numbers_protected: int = 0
    numbers_unrestored: list[str] = field(default_factory=list)


#: Encodings worth trying, in order. Chinese Windows text files are often GBK, while
#: anything produced by modern tooling is UTF-8.
ENCODINGS = ("utf-8-sig", "utf-8", "gb18030", "utf-16", "cp1251", "latin-1")

#: Extensions the import button accepts.
TEXT_SUFFIXES = (".txt", ".md", ".srt", ".vtt", ".csv", ".tsv", ".log", ".json", ".xml",
                 ".html", ".htm", ".yaml", ".yml", ".ini", ".rtf")

#: Imported files whose line breaks are structure, not wrapping. The caller can pass
#: these straight through instead of asking detect_mode to guess.
STRUCTURED_SUFFIXES = (".srt", ".vtt", ".csv", ".tsv")


def read_text_file(path: str | Path) -> str:
    """Read a text file, trying the encodings a real-world file might use."""
    raw = Path(path).read_bytes()
    for encoding in ENCODINGS:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", "replace")


def load_glossary(cfg: AppConfig) -> Glossary | None:
    """The built-in trade terms merged with the user's own rules.

    The built-in file is what makes the app useful on a contract before the user has
    written a rule of their own, so it has to be merged in here: every translation path -
    the text tab, .docx, .xlsx - comes through this function, and loading only the user's
    file left the 124 shipped trade terms doing nothing at all.
    """
    if not cfg.glossary_enabled:
        return None
    try:
        glossary = load_with_builtin(cfg.resolved_glossary_path())
    except GlossaryError:
        return None
    return glossary or None


def detect_mode(text: str) -> str:
    """Guess whether a line is a record, or just where the text happened to wrap.

    Only two shapes count as structured, and both need most of their lines to carry the
    marker: subtitle cues, and tab-separated rows. Anything else is treated as prose,
    because joining a paragraph back together is safe while splitting a sentence in half
    is not.
    """
    lines = [line for line in text.split("\n") if line.strip()]
    if not lines:
        return "paragraph"
    enough = max(1, len(lines) // 2)
    if sum(1 for line in lines if "-->" in line) >= enough:
        return "line"
    if sum(1 for line in lines if "\t" in line) >= enough:
        return "line"
    return "paragraph"


class Translator:
    """A loaded engine plus the glossary, so one run loads the model exactly once.

    Callable: give it a sentence, get the translation, with the term glossary applied on
    the way in and on the way out. Shared by the text tab and the document translator.
    """

    def __init__(self, cfg: AppConfig, models_root: str) -> None:
        from . import engine as engine_mod

        self.cfg = cfg
        self.route = engine_mod.build(cfg, models_root)
        self.route.load()
        self.glossary = load_glossary(cfg)
        self.src_lang = cfg.source_lang
        self.tgt_lang = cfg.target_lang

        # Only for engines that send the text away. The offline one has nothing to hide
        # from, and masking would only put placeholders in front of a model that has
        # enough trouble with ordinary words.
        self.redactor = None
        if getattr(cfg, "redact_enabled", True):
            engine_id = engine_mod.resolve_id(cfg.translate.engine, models_root)
            if engine_mod.is_online(engine_id):
                self.redactor = Redactor()
        #: How many things have been hidden so far, and the placeholders an engine never
        #: gave back. A dropped placeholder has to be visible: shipping a translation
        #: with [[C1]] in the middle of it is worse than not masking at all.
        self.redacted_count = 0
        self.unrestored: list[str] = []
        #: Numbers taken out of the translator's reach, and any placeholder it dropped.
        self.numbers_protected = 0
        self.numbers_unrestored: list[str] = []

    def __call__(self, unit: str) -> str:
        prepared = (self.glossary.apply_source(unit, self.src_lang, self.tgt_lang)
                    if self.glossary else unit)
        prepared = numbers.normalize(prepared, self.src_lang)
        # Numbers out of the model's reach entirely. The English hop does not treat a long
        # digit run as a number - "600000" and "600 000" both come back as "600,0" - and a
        # quotation whose amounts are wrong is worse than no quotation. See app/numbers.py.
        # Same rule as the screen path: an instruction model reads digits itself and gets
        # confused by placeholders. Measured on a table cell, "[N1] календарных дней" came
        # back as "日历日" - the number silently gone.
        masks = getattr(self.route, "masks_numbers", True)
        prepared, number_map = numbers.mask(prepared) if masks else (prepared, {})
        if number_map:
            self.numbers_protected += len(number_map)
        # Privacy masking is the last step before the text leaves, so it sees whatever the
        # glossary and the number pass turned the source into.
        outgoing, mapping = (self.redactor.mask(prepared)
                             if self.redactor else (prepared, {}))
        if mapping:
            self.redacted_count += len(mapping)
        translated = self.route.translate(outgoing)
        if mapping and translated:
            translated, missing = Redactor.unmask(translated, mapping)
            for token in missing:
                if token not in self.unrestored:
                    self.unrestored.append(token)
        if number_map and translated:
            translated, lost = numbers.unmask(translated, number_map)
            for token in lost:
                if token not in self.numbers_unrestored:
                    self.numbers_unrestored.append(token)
        if self.glossary and translated:
            translated = self.glossary.apply_target(translated, self.src_lang,
                                                    self.tgt_lang, source=unit)
        # Last pass, shared with the screen path. Without it Word/Excel/PDF/image output
        # kept "预付款款", "百分比" and "人民币" while the screen path had them fixed -
        # the same model giving different quality depending on which button was pressed.
        if translated:
            translated = finalise(translated, unit)
        return translated or unit


def translate_text(
    cfg: AppConfig,
    models_root: str,
    text: str,
    on_progress: Callable[[int, int], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
    mode: str = "auto",
) -> TextResult:
    """Translate text, rebuilding hard-wrapped paragraphs first.

    The mode argument is "auto" (decide from the text), "paragraph" or "line".
    """
    translate_unit = Translator(cfg, models_root)
    chosen = detect_mode(text) if mode == "auto" else mode
    started = time.perf_counter()
    failed: list[int] = []

    if chosen == "line":
        raw_lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        todo = [index for index, line in enumerate(raw_lines) if line.strip()]
        out = list(raw_lines)
        for done, index in enumerate(todo, start=1):
            if should_stop is not None and should_stop():
                break
            line = raw_lines[index].strip()
            try:
                out[index] = translate_unit(line)
            except TranslateError as exc:
                failed.append(index + 1)
                out[index] = f"[第 {index + 1} 行翻译失败：{exc}]"
            except Exception as exc:  # noqa: BLE001
                failed.append(index + 1)
                out[index] = f"[第 {index + 1} 行翻译失败：{exc}]"
            if on_progress is not None:
                on_progress(done, len(todo))
        return TextResult(
            source=text,
            translation="\n".join(out),
            lines=len(todo),
            sentences=len(todo),
            mode="line",
            failed=failed,
            elapsed=time.perf_counter() - started,
            redacted=translate_unit.redacted_count,
            unrestored=list(translate_unit.unrestored),
            numbers_protected=translate_unit.numbers_protected,
            numbers_unrestored=list(translate_unit.numbers_unrestored),
        )

    # Paragraph mode. The plan is fixed up front so progress can be reported against a
    # known total instead of an unknown remainder.
    blocks = rebuild_paragraphs(text)
    plan: list[tuple[int, str]] = []
    for block_index, block in enumerate(blocks):
        if block.kind == "blank":
            continue
        for sentence in split_sentences(block.text):
            plan.append((block_index, sentence))

    rendered: dict[int, list[str]] = {}
    for done, (block_index, sentence) in enumerate(plan, start=1):
        if should_stop is not None and should_stop():
            break
        try:
            piece = translate_unit(sentence)
        except TranslateError as exc:
            failed.append(done)
            piece = f"[第 {done} 句翻译失败：{exc}]"
        except Exception as exc:  # noqa: BLE001
            failed.append(done)
            piece = f"[第 {done} 句翻译失败：{exc}]"
        rendered.setdefault(block_index, []).append(piece)
        if on_progress is not None:
            on_progress(done, len(plan))

    out_blocks: list[str] = []
    for block_index, block in enumerate(blocks):
        if block.kind == "blank":
            out_blocks.append("")
        else:
            out_blocks.append(join_units(rendered.get(block_index, [])))

    return TextResult(
        source=text,
        translation="\n".join(out_blocks),
        lines=sum(1 for block in blocks if block.kind == "para"),
        sentences=len(plan),
        mode="paragraph",
        failed=failed,
        elapsed=time.perf_counter() - started,
        redacted=translate_unit.redacted_count,
        unrestored=list(translate_unit.unrestored),
        numbers_protected=translate_unit.numbers_protected,
        numbers_unrestored=list(translate_unit.numbers_unrestored),
    )


def swap_languages(cfg: AppConfig) -> None:
    """Reverse the direction, for the swap button."""
    cfg.source_lang, cfg.target_lang = cfg.target_lang, cfg.source_lang
