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
from .translator import TranslateError, TranslationRoute
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
        pair = (cfg.source_lang.strip().lower(), cfg.target_lang.strip().lower())
        wanted = (cfg.translate.engine, pair)
        if self._translator is not None and self._pair == wanted:
            # Beam count can change without rebuilding the ONNX sessions.
            self._translator.set_beams(cfg.translate.num_beams)
            return self._translator

        from . import engine as engine_mod

        root = cfg.resolved_models_root()
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
        if text != self._pending:
            self._pending = text
            self._pending_count = 1
        else:
            self._pending_count += 1
        return self._pending_count >= max(1, cfg.settle_frames)

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

                result = ocr.recognize(frame)
                ocr_ms = result.elapsed * 1000
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
                glossary = self._ensure_glossary(cfg)
                # The lines as the recogniser saw them, not the flattened string: the
                # layout is the only thing that tells the reader which part of the page
                # a translation came from.
                line_texts = [ln.text for ln in result.lines if ln.text.strip()]
                if not line_texts:
                    line_texts = [text]
                try:
                    translation, translate_ms, sent = self._translate_units(
                        line_texts, translator, glossary, cfg)
                    self._last_translation = translation
                    self._last_translated = sent
                    changed = True
                except TranslateError as exc:
                    self._error(str(exc))
                except Exception as exc:  # noqa: BLE001
                    self._error(f"翻译失败：{exc}", traceback.format_exc())

            self._emit(Event(
                kind=RESULT,
                source=text,
                translation=translation,
                changed=changed,
                capture_ms=capture_ms,
                ocr_ms=ocr_ms,
                translate_ms=translate_ms,
            ))
            self._emit(Event(
                kind=STATS,
                capture_ms=capture_ms,
                ocr_ms=ocr_ms,
                translate_ms=translate_ms,
            ))
            self._sleep_rest(cycle_start, interval)

        self._status("后台线程已停止")


    def _translate_units(self, line_texts, translator, glossary, cfg):
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
            if units and _merges(units[-1], line):
                units[-1] = _join_units_text(units[-1], line)
            else:
                units.append(line)
        if not units:
            return "", 0.0, 0

        started = time.perf_counter()
        pieces: list[str] = []
        sent = 0
        for unit in units:
            cached = self._unit_cache.get(unit)
            if cached is not None:
                pieces.append(cached)
                continue
            src = unit
            if glossary is not None:
                # Source-side rules normalise what the engine sees; target-side rules
                # patch what comes back. Order matters, so they stay separate.
                src = glossary.apply_source(src, cfg.source_lang, cfg.target_lang)
            piece = translator.translate(src)
            if glossary is not None and piece:
                piece = glossary.apply_target(piece, cfg.source_lang, cfg.target_lang,
                                              source=unit)
            self._unit_cache[unit] = piece
            sent += 1
            pieces.append(piece)

        # Bounded, so a long session in front of a scrolling feed cannot grow forever.
        while len(self._unit_cache) > 4000:
            self._unit_cache.pop(next(iter(self._unit_cache)))
        return "\n".join(pieces), (time.perf_counter() - started) * 1000, sent

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
