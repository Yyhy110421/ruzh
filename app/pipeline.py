"""The capture -> OCR -> translate worker.

Runs on its own thread and communicates exclusively through a queue so the Tk
main loop never blocks. Everything heavy (ONNX sessions, the OCR engine) is
constructed here, on this thread, the first time it is needed.
"""

from __future__ import annotations

import difflib
import os
import queue
import threading
import time
import traceback
from dataclasses import dataclass, field

from .capture import ScreenCapture
from .config import AppConfig, Region
from .glossary import Glossary, GlossaryError, load_with_builtin
from .ocr_engine import OcrEngine, script_for
from .translator import TranslateError, TranslationRoute, finalise, repair_output
from .window_target import resolve_region, target_window_label

#: Event kinds pushed onto the output queue.
RESULT = "result"
STATUS = "status"
ERROR = "error"
STATS = "stats"
#: The capture rectangle moved - the UI repositions the overlay to match.
TARGET = "target"


@dataclass
class Event:
    kind: str
    source: str = ""
    translation: str = ""
    message: str = ""
    ocr_ms: float = 0.0
    translate_ms: float = 0.0
    capture_ms: float = 0.0
    changed: bool = False
    meta: dict = field(default_factory=dict)


def _differing_chars(a: str, b: str) -> int:
    """How many characters of the longer read are not matched by the other."""
    matcher = difflib.SequenceMatcher(None, a, b, autojunk=False)
    matched = sum(block.size for block in matcher.get_matching_blocks())
    return max(len(a), len(b)) - matched


def _is_jitter(a: str, b: str, tolerance: float) -> bool:
    """True when two reads differ only by OCR noise rather than by wording.

    Ratios mislead on short strings - a single wrong character in "Hello world."
    scores 0.92 - so the allowance is expressed as a small number of characters.
    """
    if not a or not b:
        return False
    allowed = max(1, min(4, int(round(tolerance * max(len(a), len(b))))))
    return _differing_chars(a, b) <= allowed


#: A unit longer than this is split at sentence ends before translation. Measured: the
#: two-hop ru->en->zh route truncates on long input - one paragraph came back as
#: "合同总额为600,0卢布，增值税会计。" whole, but as two correct sentences when split.
UNIT_MAX_CHARS = 90


import re

#: Entities the engines emit instead of the character they mean. OPUS turned an apostrophe
#: into "& aspos;" on screen, which is not a typo the user can be expected to decode.
_ENTITY = re.compile(r"&\s?(apos|aspos|quot|amp|lt|gt|nbsp|#39|#34);", re.I)
_ENTITY_CHAR = {"apos": "'", "aspos": "'", "quot": '"', "amp": "&", "lt": "<", "gt": ">",
                "nbsp": " ", "#39": "'", "#34": '"'}


def _strip_entities(text: str) -> str:
    """Decode the handful of entities a model emits, and drop the space around them."""
    if "&" not in text:
        return text
    text = _ENTITY.sub(lambda m: _ENTITY_CHAR.get(m.group(1).lower(), ""), text)
    return re.sub(r"\s{2,}", " ", text).strip()


#: Money words the models get wrong. Gated on the SOURCE containing the Russian term, so a
#: sentence that really is about yuan is left alone.
_CURRENCY_FIX = (
    ("\u0440\u0443\u0431\u043b", ("\u4eba\u6c11\u5e01", "\u5143"), "\u5362\u5e03"),
)


def _fix_currency(text: str, source: str) -> str:
    """Force the Russian rouble into 卢布 when the source is about roubles."""
    if not text or not source:
        return text
    low = source.lower()
    for marker, wrong_words, right in _CURRENCY_FIX:
        if marker not in low:
            continue
        for wrong in wrong_words:
            if wrong in text:
                text = text.replace(wrong, right)
    return text


def _split_long_unit(unit: str) -> list[str]:
    """Cut an over-long unit at sentence (or clause) boundaries.

    Only splits when there is something to split on; a single long run with no punctuation
    is left alone rather than cut mid-word.
    """
    if len(unit) <= UNIT_MAX_CHARS:
        return [unit]
    for pattern in (r"(?<=[.!?。！？])\s+", r"(?<=[;；])\s+", r"(?<=[,，])\s+"):
        parts = [p.strip() for p in re.split(pattern, unit) if p.strip()]
        if len(parts) > 1:
            return parts
    return [unit]



def _ocr_rows(lines) -> list[str]:
    """Group OCR boxes on the same horizontal row back into one line.

    A menu bar - File  View  Repository  Branch  Help - comes back as five separate boxes
    at the same y. Treating those as five units is what made a horizontal menu translate
    into a vertical list of single words.

    Rows are matched by vertical proximity rather than exact y, because OCR gives each box
    a slightly different top depending on the glyphs; 8 px is the tolerance floor.
    """
    items = []
    for line in lines or []:
        text = (getattr(line, "text", "") or "").strip()
        if not text:
            continue
        # box is a numpy array on the real path, so "or []" is ambiguous - test length.
        box = getattr(line, "box", None)
        has_box = box is not None and len(box) > 0
        if has_box:
            top = min(point[1] for point in box)
            height = max(point[1] for point in box) - top
            left = min(point[0] for point in box)
        else:
            top = float(getattr(line, "top", 0.0) or 0.0)
            left = float(getattr(line, "left", 0.0) or 0.0)
            height = 0.0
        items.append((top, left, max(height, 1.0), text))
    if not items:
        return []
    items.sort(key=lambda item: (item[0], item[1]))

    rows: list[dict] = []
    for top, left, height, text in items:
        for row in rows:
            tolerance = max(8.0, 0.5 * min(height, row["height"]))
            if abs(top - row["top"]) <= tolerance:
                row["cells"].append((left, text))
                break
        else:
            rows.append({"top": top, "height": height, "cells": [(left, text)]})

    # A screen has columns; a list of lines does not. GitHub Desktop puts a left panel
    # (search box, "Your repositories", the repo list) beside a right panel (four buttons),
    # and reading them as one top-to-bottom run interleaved the two - reported as "分行".
    # Rows are therefore ordered by side then by height, so everything from the left panel
    # comes out together and everything from the right panel comes out together.
    lefts = [min(cell[0] for cell in row["cells"]) for row in rows]
    middle = (min(lefts) + max(lefts)) / 2.0 if lefts else 0.0

    def order(row) -> tuple[int, float]:
        side = 0 if min(cell[0] for cell in row["cells"]) <= middle else 1
        return side, row["top"]

    joined = []
    for row in sorted(rows, key=order):
        row["cells"].sort(key=lambda cell: cell[0])
        row_text = row["cells"][0][1]
        for _, cell in row["cells"][1:]:
            row_text = _join_units_text(row_text, cell)
        joined.append(row_text)
    return joined



def _layout_rows(items):
    """Turn positioned blocks into text rows that keep their columns.

    The pane used to receive one flat list, so a window with a left column and a right
    column came out as a single vertical run. Blocks whose tops line up belong to the
    same row; inside a row they keep their left-to-right order, separated by tabs.
    """
    if not items:
        return ""
    rows = []
    for item in sorted(items, key=lambda i: (float(i[1]), float(i[0]))):
        top, left, text = float(item[1]), float(item[0]), str(item[4]).strip()
        if not text:
            continue
        height = float(item[3]) or 20.0
        if rows and abs(top - rows[-1][0]) <= max(6.0, height * 0.6):
            rows[-1][1].append((left, text))
        else:
            rows.append([top, [(left, text)]])
    lines = []
    for _top, cells in rows:
        cells.sort(key=lambda c: c[0])
        lines.append(chr(9).join(text for _left, text in cells))
    return chr(10).join(lines)


def _ocr_rows_meta(lines) -> list[dict]:
    """Rows with their geometry: {text, left, top, width, height}.

    A screen is not a column of paragraphs - it has a left panel and a right panel, and
    each block has its own box. Carrying the box through to the overlay is what lets the
    translation be drawn where the original was, with the font scaled to fit that box
    instead of one flat list of lines.
    """
    items = []
    for line in lines or []:
        text = (getattr(line, "text", "") or "").strip()
        if not text:
            continue
        box = getattr(line, "box", None)
        if box is None or len(box) == 0:
            continue
        xs = [float(point[0]) for point in box]
        ys = [float(point[1]) for point in box]
        items.append({"text": text, "left": min(xs), "top": min(ys),
                      "width": max(1.0, max(xs) - min(xs)),
                      "height": max(1.0, max(ys) - min(ys))})
    # Row grouping needs top-then-left (boxes on one row must meet), so this stays; the
    # column order is applied to the finished rows below.
    items.sort(key=lambda item: (item["top"], item["left"]))

    rows: list[dict] = []
    for item in items:
        for row in rows:
            tolerance = max(8.0, 0.5 * min(item["height"], row["height"]))
            if abs(item["top"] - row["top"]) <= tolerance:
                row["boxes"].append(item)
                break
        else:
            rows.append({"top": item["top"], "height": item["height"], "boxes": [item]})
    # A screen has columns; a list of lines does not. Sort the finished rows by side first,
    # the same rule _ocr_rows uses, or the left panel and the right panel interleave.
    if rows:
        row_lefts = [min(box["left"] for box in row["boxes"]) for row in rows]
        middle = (min(row_lefts) + max(row_lefts)) / 2.0
    else:
        middle = 0.0
    rows.sort(key=lambda row: (0 if min(box["left"] for box in row["boxes"]) <= middle else 1,
                               row["top"]))

    out: list[dict] = []
    for row in rows:
        boxes = sorted(row["boxes"], key=lambda item: item["left"])
        left = min(box["left"] for box in boxes)
        top = min(box["top"] for box in boxes)
        right = max(box["left"] + box["width"] for box in boxes)
        bottom = max(box["top"] + box["height"] for box in boxes)
        text = boxes[0]["text"]
        for box in boxes[1:]:
            text = _join_units_text(text, box["text"])
        out.append({"text": text, "left": left, "top": top,
                    "width": max(1.0, right - left), "height": max(1.0, bottom - top)})
    return out


def _merges(previous: str, following: str) -> bool:
    """Is this line a wrapped continuation of the one above it?

    The old rule was "merge whenever the previous line does not end a sentence", and it is
    wrong for exactly the case that matters here: a web page's nav item, heading or table
    row does not end with punctuation either, so thirty of them collapsed into one run.

    A Russian (or English) sentence starts with a capital, so the strongest signal for a
    wrap is that the next line starts lowercase - "и ООО Лютик о поставке..." after
    "...между ООО Ромашка". Nothing else is a continuation.

    Chinese has no case, so nothing merges there: keeping the lines is what a reader of a
    Chinese page wants anyway.
    """
    from .ocr_engine import is_cjk

    previous = previous.strip()
    following = following.strip()
    if not previous or not following:
        return False
    head = following[0]
    if is_cjk(head) or not head.islower():
        return False
    return not _ends_sentence(previous)


def _ends_sentence(line: str) -> bool:
    from .segment import ends_sentence

    return ends_sentence(line)


def _join_units_text(left: str, right: str) -> str:
    """Glue two wrapped halves, without a space where CJK meets CJK."""
    from .ocr_engine import is_cjk

    if is_cjk(left[-1]) and is_cjk(right[0]):
        return left + right
    return left + " " + right


class Pipeline(threading.Thread):
    def __init__(self, cfg: AppConfig, out: queue.Queue) -> None:
        super().__init__(name="pipeline", daemon=True)
        self._cfg = cfg
        self._cfg_lock = threading.Lock()
        self._out = out
        self._stop_event = threading.Event()
        # Same text, same answer. A static screen re-sends identical blocks every
        # couple of seconds; translating them again costs the full 4 s per block
        # for no change in output.
        self._memo: dict[str, str] = {}
        self._pause_event = threading.Event()
        self._pause_announced = False

        self._capture = ScreenCapture()
        self._ocr: OcrEngine | None = None
        self._translator: TranslationRoute | None = None
        self._pair: tuple[str, str] | None = None
        self._ocr_settings_key: tuple | None = None
        self._glossary: Glossary | None = None
        self._glossary_key: tuple | None = None
        #: Last resolved capture rectangle, so a move can be announced once.
        self._target: tuple[int, int, int, int] | None = None

        self._last_committed = ""
        self._last_seen = ""
        self._last_translation = ""
        self._pending = ""
        self._pending_count = 0
        self._defer_count = 0
        #: When the current _pending text was first held, for the settle safety valve.
        self._pending_since = 0.0
        #: Translate anyway once a read has been held this long, whatever settle_frames says.
        self.settle_budget = 4.0
        self._blank_count = 0
        self._error_streak = 0
        #: Translated units, keyed by their source text. A page scrolls, a cursor blinks,
        #: one row of a table changes - retranslating the other twenty lines because of it
        #: is most of why this felt slow. Cleared whenever the direction or engine changes.
        self._unit_cache: dict[str, str] = {}
        self._unit_key: tuple = ()
        #: How many units the last frame actually sent to the engine (not how many it
        #: had), which is the number that explains a slow first frame versus a fast one.
        self._last_translated = 0
        #: Downsampled previous frame, for "has the screen actually changed".
        self._last_frame_small = None
        #: Last OCR result, reused while the screen is unchanged.
        self._last_ocr = None
        #: How many NEW units one cycle may translate. Each is a separate inference
        #: (~0.9 s for en->zh, more for the two-hop pairs), so a window with forty lines
        #: would otherwise take half a minute before the first word appeared. The extras
        #: are filled in by the following cycles, which cost almost nothing for the lines
        #: already cached.
        # 12, not 5. At five the first screenful came out half English and half
        # translated, which reads as a broken feature even though the next cycle fills the
        # rest in: the user sees the snapshot, not the eventual state. A typical UI window
        # is 8-12 units, so 12 finishes one in a single pass. Measured cost: 12 x ~3 s for
        # the local model, ~11 s for OPUS.
        self.unit_budget = 12
        self._last_items: list[tuple] = []

    # -- public API --------------------------------------------------------
    def update_config(self, cfg: AppConfig) -> None:
        with self._cfg_lock:
            self._cfg = cfg

    def stop(self) -> None:
        """Ask the worker to finish.

        It cannot be instantaneous: the loop may be inside an OCR or translation call,
        or still loading a model, and none of those can be interrupted safely. Callers
        should wait for :meth:`is_alive` to go false rather than assume it stopped -
        starting a second pipeline while this one is winding down would run two
        captures at once and load a second copy of the models.
        """
        self._pause_event.clear()
        self._stop_event.set()
        # Stopping screen translation is the natural point to give the memory back; the
        # local model server is restarted lazily the next time it is selected.
        try:
            from .engines import local_llm as _local_llm
            _local_llm.stop_server()
        except Exception:  # noqa: BLE001
            pass

    def pause(self) -> None:
        """Suspend work without unloading the models."""
        self._pause_event.set()

    def resume(self) -> None:
        self._pause_event.clear()

    @property
    def paused(self) -> bool:
        return self._pause_event.is_set()

    @property
    def stopping(self) -> bool:
        return self._stop_event.is_set()

    def _snapshot(self) -> AppConfig:
        with self._cfg_lock:
            return self._cfg

    def _emit(self, event: Event) -> None:
        try:
            self._out.put_nowait(event)
        except queue.Full:
            pass

    # -- helpers -----------------------------------------------------------
    def _status(self, message: str) -> None:
        self._emit(Event(kind=STATUS, message=message))

    def _error(self, message: str, detail: str = "") -> None:
        self._emit(Event(kind=ERROR, message=message, meta={"detail": detail}))

    def _pair_items(self, rows_meta) -> list[tuple]:
        """(left, top, width, height, translated) for every row, or [] if lengths differ.

        The overlay draws one label per entry at the original box with the font scaled to
        fit. A mismatch means the unit list was merged or split, and pairing then would put
        one paragraph's translation under another box - worse than not drawing at all.
        """
        pieces = getattr(self, "_last_pieces", []) or []
        if not rows_meta or len(pieces) != len(rows_meta):
            return []
        return [(row["left"], row["top"], row["width"], row["height"], piece)
                for row, piece in zip(rows_meta, pieces)]

    def _ensure_ocr(self, cfg: AppConfig) -> OcrEngine:
        # The script is part of the key: switching from English to Russian has to
        # swap in the Cyrillic recognition model, and switching back has to undo it.
        script = script_for(cfg.source_lang)
        key = (
            cfg.ocr.min_score,
            cfg.ocr.max_side_len,
            cfg.ocr.intra_op_threads,
            script,
        )
        if self._ocr is None or self._ocr_settings_key != key:
            self._status("正在加载 OCR 模型…")
            engine = OcrEngine(cfg.ocr, models_root=cfg.resolved_models_root(),
                               script=script)
            engine.load()
            # Warm up before reporting ready. The first real inference pays one-off costs
            # (graph re-optimisation for the actual shape, arena allocation) that measured
            # 5.7 s on this machine - spending it here keeps it out of the user's first
            # frame, and the status line already says a model is loading.
            engine.warmup()
            self._ocr = engine
            self._ocr_settings_key = key
            note = f"（{script} 识别模型）" if engine.uses_script_model else ""
            self._status(f"OCR 就绪（{engine.load_seconds:.1f}s）{note}")
            if script != "latin" and not engine.uses_script_model:
                # Better to say it plainly than to emit lookalike-Latin nonsense.
                self._status(
                    f"提示：还没下载 {script} 识别模型，"
                    f"{cfg.source_lang} 文字会被认成相似字母。"
                    f"用 scripts\\setup_models.py 下载。")
            if engine.load_error:
                self._status(engine.load_error)
        return self._ocr

    def _ensure_glossary(self, cfg: AppConfig):
        """Load the term glossary, reloading when the file or setting changes.

        Keyed on mtime so a user editing glossary.json sees the change on the next
        subtitle instead of having to restart the app.
        """
        if not cfg.glossary_enabled:
            self._glossary = None
            self._glossary_key = None
            return None

        path = cfg.resolved_glossary_path()
        try:
            stamp = os.path.getmtime(path)
        except OSError:
            stamp = 0.0
        key = (path, stamp)
        if self._glossary_key != key:
            try:
                # Merged, not just the user's file: the shipped trade terms are the
                # point of having a glossary on a fresh install.
                loaded = load_with_builtin(path)
            except GlossaryError as exc:
                self._error(f"词库加载失败：{exc}")
                loaded = None
            self._glossary = loaded
            self._glossary_key = key
            if loaded is not None:
                stats = loaded.stats()
                self._status(f"词库已加载：{stats['enabled']}/{stats['total']} 条生效")
                for problem in loaded.problems[:3]:
                    self._status(f"词库问题：{problem}")
        return self._glossary

    def _ensure_translator(self, cfg: AppConfig):
        from . import engine as engine_mod

        pair = (cfg.source_lang.strip().lower(), cfg.target_lang.strip().lower())
        root = cfg.resolved_models_root()
        # Key the cache on the *resolved* engine, not the configured string. An empty
        # engine means "choose automatically", and resolving it writes the chosen id back
        # into cfg - so keying on the raw value made the second cycle see "" -> "opus" as
        # a change and rebuild the whole ONNX route, costing a measured 2-4 s of model
        # loading in the middle of a session with nothing actually changed.
        try:
            resolved = engine_mod.resolve_id(cfg.translate.engine, root)
        except Exception:  # noqa: BLE001
            resolved = cfg.translate.engine
        wanted = (resolved, pair)
        if self._translator is not None and self._pair == wanted:
            # Beam count can change without rebuilding the ONNX sessions.
            self._translator.set_beams(cfg.translate.num_beams)
            return self._translator

        # Switching away from the local model frees its server: a 1.5B model holds about
        # 1 GB resident, and measured on this 7.7 GB machine that was enough to make a
        # later model load die with MemoryError.
        if resolved != "local-llm" and self._translator is not None:
            try:
                from .engines import local_llm as _local_llm
                if _local_llm.stop_server():
                    self._status("本地大模型已卸载")
            except Exception:  # noqa: BLE001
                pass

        ready, detail = engine_mod.check(cfg, root)
        if not ready:
            raise TranslateError(engine_mod.missing_summary(cfg, root) or detail)

        self._status(f"正在加载翻译模型 {pair[0]}→{pair[1]}…")
        route = engine_mod.build(cfg, root)
        route.load()
        route.set_beams(cfg.translate.num_beams)
        self._translator = route
        self._pair = wanted
        self._status(f"翻译模型就绪（{route.load_seconds:.1f}s）{route.label}")
        return route

    def _should_commit(self, text: str, cfg: AppConfig) -> bool:
        previous = self._last_seen
        self._last_seen = text

        if text == self._last_committed:
            self._defer_count = 0
            return False

        if self._last_committed and _is_jitter(text, self._last_committed, cfg.jitter_tolerance):
            # OCR noise on the same line: keep the current translation *and* keep
            # comparing against the text that translation came from. Absorbing the
            # new text here would let a line that grows one character at a time be
            # mistaken for noise forever, leaving a stale translation on screen.
            self._defer_count = 0
            return False

        # A subtitle that types itself out frame by frame reads as a run of longer
        # prefixes. Translating each intermediate state wastes a full inference and
        # makes the overlay flicker, so hold off while the text is still changing and
        # only ever extends or truncates the previous read.
        still_typing = (
            cfg.defer_while_growing
            and previous
            and previous != text
            and (text.startswith(previous) or previous.startswith(text))
        )
        if still_typing:
            self._defer_count += 1
            if self._defer_count <= max(1, cfg.max_defer_frames):
                self._pending = text
                self._pending_count = 0
                return False

        self._defer_count = 0
        # Stability is judged with the same tolerance the jitter filter above uses, not by
        # byte equality. Byte equality never happens on a live window - antialiased text
        # gives a slightly different read almost every frame - so with settle_frames=5 the
        # gate stayed shut and the program sat there recognising forever without ever
        # translating, which is exactly "翻了半天没翻出来".
        now = time.perf_counter()
        if self._pending and _is_jitter(text, self._pending, cfg.jitter_tolerance):
            self._pending_count += 1
        else:
            self._pending = text
            self._pending_count = 1
        if not self._pending_since:
            self._pending_since = now

        settled = self._pending_count >= max(1, cfg.settle_frames)
        # Safety valve. settle_frames is a preference about *when* to translate, never a
        # reason not to: on a slow OCR every frame costs seconds, so N frames can mean a
        # minute of nothing. Past this, translate what is on screen.
        overdue = (now - self._pending_since) >= self.settle_budget
        if settled or overdue:
            self._pending_since = 0.0
            return True
        return False

    # -- main loop ---------------------------------------------------------
    def run(self) -> None:  # noqa: C901 - a long but linear worker loop
        self._status("后台线程已启动")
        while not self._stop_event.is_set():
            cfg = self._snapshot()
            interval = max(0.15, cfg.interval_ms / 1000.0)

            # Paused: keep the thread and the loaded models, do no work. Stopping and
            # restarting instead would throw away the ONNX sessions and pay several
            # seconds of model loading again.
            if self._pause_event.is_set():
                if not self._pause_announced:
                    self._pause_announced = True
                    self._status("已暂停（模型保持加载，继续即可秒开）")
                self._stop_event.wait(0.2)
                continue
            if self._pause_announced:
                self._pause_announced = False
                self._status("已继续")

            # Re-resolved every frame: in window mode this is what makes the capture
            # follow the window when the user drags it somewhere else.
            target = resolve_region(cfg)
            if target[2] < 8 or target[3] < 8:
                if cfg.target_mode == "window":
                    self._status(f"目标窗口不可用：{target_window_label(cfg) or '未选择'}")
                else:
                    self._status("请先框选要翻译的屏幕区域")
                self._stop_event.wait(0.5)
                continue
            if self._target != target:
                self._target = target
                self._emit(Event(kind=TARGET, meta={"region": target}))

            cycle_start = time.perf_counter()
            try:
                ocr = self._ensure_ocr(cfg)
                translator = self._ensure_translator(cfg)
            except TranslateError as exc:
                self._error(str(exc))
                self._stop_event.wait(2.0)
                continue
            except Exception as exc:  # noqa: BLE001
                self._error(f"初始化失败：{exc}", traceback.format_exc())
                self._stop_event.wait(2.0)
                continue

            try:
                t0 = time.perf_counter()
                frame = self._grab(cfg, target)
                capture_ms = (time.perf_counter() - t0) * 1000

                if self._screen_changed(frame):
                    # Strip-level reuse: when only part of the window moved - one new
                    # chat message, one table row - the untouched strips keep their
                    # previous reading instead of going back through the model.
                    result = ocr.recognize_cached(frame)
                    self._last_ocr = result
                    ocr_ms = result.elapsed * 1000
                else:
                    # Nothing moved enough to be worth reading again. Reusing the previous
                    # result is what makes a still screen cost nothing instead of the full
                    # several seconds of recognition.
                    result = self._last_ocr
                    ocr_ms = 0.0
                    if result is None:
                        self._sleep_rest(cycle_start, interval)
                        continue
            except Exception as exc:  # noqa: BLE001
                self._error_streak += 1
                if self._error_streak <= 2:
                    self._error(f"截屏/识别失败：{exc}", traceback.format_exc())
                self._stop_event.wait(1.0)
                continue
            self._error_streak = 0

            text = result.text.strip()
            if not text:
                self._blank_count += 1
                if self._blank_count == max(1, cfg.blank_clear_frames):
                    self._last_committed = ""
                    self._last_seen = ""
                    self._last_translation = ""
                    self._pending = ""
                    self._pending_count = 0
                    self._defer_count = 0
                    self._pending_since = 0.0
                    self._emit(Event(kind=RESULT, changed=True, capture_ms=capture_ms, ocr_ms=ocr_ms))
                self._sleep_rest(cycle_start, interval)
                continue
            self._blank_count = 0

            changed = False
            translation = self._last_translation
            translate_ms = 0.0
            if self._should_commit(text, cfg):
                # Checked here as well as at the top of the loop: OCR can take a second
                # and a translation several more, and the user expects Stop to bite
                # within a frame rather than after the current sentence finishes.
                if self._stop_event.is_set() or self._pause_event.is_set():
                    break
                self._last_committed = text
                self._pending = text
                self._pending_count = 0
                self._pending_since = 0.0
                glossary = self._ensure_glossary(cfg)
                # The lines as the recogniser saw them, not the flattened string: the
                # layout is the only thing that tells the reader which part of the page
                # a translation came from.
                rows_meta = _ocr_rows_meta(result.lines)
                line_texts = [row["text"] for row in rows_meta] or [text]
                try:
                    translation, translate_ms, sent, shown = self._translate_units(
                        line_texts, translator, glossary, cfg, merge=False)
                    self._last_items = self._pair_items(rows_meta)
                    if shown:
                        text = shown
                    if sent >= self.unit_budget:
                        # There was more left than this cycle's budget. Do not mark the
                        # frame as done, or the gate below treats the half-translated
                        # page as finished and the rest is never filled in.
                        self._last_committed = ""
                    self._last_translation = translation
                    self._last_translated = sent
                    changed = True
                except TranslateError as exc:
                    self._error(str(exc))
                except Exception as exc:  # noqa: BLE001
                    self._error(f"翻译失败：{exc}", traceback.format_exc())

            # One source block per line. The pane used to receive the blocks joined by
            # spaces, so "识别的原文" showed one long run while 译文 was correctly broken
            # into twelve - that mismatch is what "分行" was about.
            # Lay the translation out the way the source sits on screen: blocks that
            # share a row stay on one line, left and right kept apart by tabs. A flat
            # list read as one vertical run and lost the columns.
            layout = _layout_rows(getattr(self, "_last_items", []))
            source_text = shown if isinstance(shown, str) and shown.strip() else text
            self._emit(Event(
                kind=RESULT,
                source=source_text,
                translation=layout or translation,
                changed=changed,
                capture_ms=capture_ms,
                ocr_ms=ocr_ms,
                translate_ms=translate_ms,
                meta={"items": getattr(self, "_last_items", [])},
            ))
            self._emit(Event(
                kind=STATS,
                capture_ms=capture_ms,
                ocr_ms=ocr_ms,
                translate_ms=translate_ms,
            ))
            self._sleep_rest(cycle_start, interval)

        self._status("后台线程已停止")


    def _screen_changed(self, frame) -> bool:
        """Did the captured frame change enough to be worth reading again?

        Recognition is the expensive half of a frame - measured at ~7 s for a 900x700
        window with 42 lines on this machine. A blinking cursor, a clock ticking or a
        spinner changes a few hundred pixels out of half a million, and re-reading the
        whole window for that is most of why the program felt slow while nothing was
        happening. Compared on a 4x downsampled copy with a per-pixel tolerance, so
        animation noise does not count as change.
        """
        import numpy as np

        try:
            small = frame[::4, ::4, :3].astype(np.int16)
        except Exception:  # noqa: BLE001
            return True
        previous = self._last_frame_small
        self._last_frame_small = small
        if previous is None or previous.shape != small.shape:
            return True
        delta = np.abs(small - previous).max(axis=2)
        changed = float((delta > 12).mean())
        return changed > 0.004

    def _log_numbers(self, unrestored, unit: str) -> None:
        """Record numbers the engine dropped, without spamming the status bar."""
        text = "、".join(str(item) for item in unrestored[:5])
        self._error(f"数字未还原：{text}（原文: {unit[:40]}）")

    def _translate_units(self, line_texts, translator, glossary, cfg, merge: bool = True):
        """Translate line by line, keeping the layout, reusing what has not changed.

        Handing the whole recognised text to the engine in one go is what made a web page
        come back as one unbroken run: the page has thirty short lines - a nav item, a
        heading, a table row - and after the join there is no way to match any part of the
        translation back to where it came from.

        So the text is split at its line breaks and a line is merged into the previous one
        only when it is a continuation of the same sentence. Same rule the text tab uses,
        so a wrapped subtitle still goes through in one piece.

        Each unit is cached by its source text: a blinking cursor, a clock ticking, one row
        of a table changing - none of those should cost a retranslation of the other
        twenty-nine lines, and that is most of why this felt slow.

        Returns (translation, milliseconds, how many units were actually sent).
        """
        key = (cfg.source_lang, cfg.target_lang,
               getattr(cfg.translate, "engine", ""), self._glossary_key)
        if key != self._unit_key:
            self._unit_cache.clear()
            self._unit_key = key

        units: list[str] = []
        for raw in line_texts:
            line = (raw or "").strip()
            if not line:
                continue
            if merge and units and _merges(units[-1], line):
                units[-1] = _join_units_text(units[-1], line)
            else:
                units.append(line)
        # A paragraph read as one box arrives as a single long unit and the offline route
        # degrades on it, so cut it at sentence ends before translating.
        if units:
            units = [piece for unit in units for piece in _split_long_unit(unit)]
        if not units:
            return "", 0.0, 0, ""

        started = time.perf_counter()
        pieces: list[str] = []
        # Kept so the caller can pair each translation with the box it came from; the
        # overlay draws one label per box instead of one flat list.
        self._last_pieces = pieces
        sent = 0
        for unit in units:
            cached = self._unit_cache.get(unit)
            if cached is not None:
                pieces.append(cached)
                continue
            if sent >= self.unit_budget:
                # Out of budget for this cycle. Show the original line rather than an
                # empty one, and leave it in the cache-miss state so the next cycle picks
                # it up: a 42-line window costs 42 inferences at ~0.9 s each, and doing
                # them all before showing anything is what "翻了半天没翻出来" felt like.
                pieces.append(unit)
                continue
            src = unit
            if glossary is not None:
                # Source-side rules normalise what the engine sees; target-side rules
                # patch what comes back. Order matters, so they stay separate.
                src = glossary.apply_source(src, cfg.source_lang, cfg.target_lang)
            # Numbers are masked before the engine sees them. Without this the screen path
            # mangled them - "600 000" came back as "600,0" - because only the document and
            # text-tab paths were masking. That is the one claim the product makes about
            # accuracy, so it belongs on every path.
            from . import numbers as numbers_mod
            # An instruction model reads digits correctly by itself and gets confused by
            # placeholders, so a route may decline the masking the Marian/NLLB routes need.
            masks = getattr(translator, "masks_numbers", True)
            try:
                masked, tokens = numbers_mod.mask(src) if masks else (src, {})
            except Exception:  # noqa: BLE001
                masked, tokens = src, {}
            cached = self._memo.get(masked)
            if cached is not None:
                piece = cached
            else:
                piece = _strip_entities(translator.translate(masked))
                self._memo[masked] = piece
            if tokens:
                try:
                    piece, unrestored = numbers_mod.unmask(piece, tokens)
                except Exception:  # noqa: BLE001
                    unrestored = list(tokens.values())
                if unrestored:
                    self._log_numbers(unrestored, unit)
            if glossary is not None and piece:
                piece = glossary.apply_target(piece, cfg.source_lang, cfg.target_lang,
                                              source=unit)
            # Cleanup runs LAST. It used to run before the glossary, which then undid it -
            # the 预付款 rule turned the repaired "预付款" back into "预付款款" and the fix
            # looked like it did nothing.
            piece = finalise(piece, src)
            self._unit_cache[unit] = piece
            sent += 1
            pieces.append(piece)

        # Bounded, so a long session in front of a scrolling feed cannot grow forever.
        while len(self._unit_cache) > 4000:
            self._unit_cache.pop(next(iter(self._unit_cache)))
        # The units come back too: the 原文 pane has to show the same breaks the
        # translation has, otherwise "分行" looks unfixed even when it is not.
        return ("\n".join(pieces), (time.perf_counter() - started) * 1000, sent,
                "\n".join(units))

    def _grab(self, cfg: AppConfig, target: tuple[int, int, int, int]):
        """Capture what the user selected, preferring the window's own drawing.

        In window mode the selection is a rectangle on screen, and a screen grab returns
        whatever is on top of it - another window, a notification, this program's own
        subtitle overlay. Measured complaint: "有东西遮挡后会翻译遮挡的部分". Asking the
        window to render itself (PrintWindow) returns its content even when it is
        covered.

        PrintWindow is refused for some processes and returns all-black for
        hardware-accelerated windows, so a screen grab is the fallback. Doing it in this
        order also means the overlay can never end up translating itself: the overlay is
        a separate top-level window and simply is not in the picture.
        """
        from . import window_target

        if getattr(cfg, "target_mode", "region") == "window":
            hwnd = window_target.find_window(
                getattr(cfg, "target_window_title", ""),
                getattr(cfg, "target_window_process", ""))
            if hwnd:
                rect = window_target.frame_rect(hwnd)
                whole = window_target.capture_frame(hwnd) if rect else None
                if whole is not None and rect is not None:
                    off_x = target[0] - rect[0]
                    off_y = target[1] - rect[1]
                    height, width = whole.shape[:2]
                    if 0 <= off_x and 0 <= off_y and off_x + target[2] <= width \
                            and off_y + target[3] <= height:
                        cropped = whole[off_y:off_y + target[3], off_x:off_x + target[2]]
                        if cropped.size:
                            return cropped.copy()

        return self._capture.grab(Region(left=target[0], top=target[1],
                                         width=target[2], height=target[3]))

    def _sleep_rest(self, cycle_start: float, interval: float) -> None:
        remaining = interval - (time.perf_counter() - cycle_start)
        if remaining > 0:
            self._stop_event.wait(remaining)
