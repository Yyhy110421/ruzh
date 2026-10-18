"""Offline OCR built on RapidOCR (PP-OCRv4 ONNX models, shipped in the wheel).

Performance notes that matter a lot for a live screen region:

* RapidOCR's default detector preprocessing uses ``limit_type="min"`` with
  ``limit_side_len=736``, which *upscales* a small subtitle strip by 5-6x. We
  override it to ``max`` so a small crop is processed as-is.
* ``width_height_ratio`` pads wide strips into a square-ish letterbox; disabled.
* The angle classifier is unnecessary for screen text and is switched off.

**Script coverage.** The bundled ``ch_PP-OCRv4_rec`` model reads Chinese and Latin
only. Fed Cyrillic it does not fail - it silently returns lookalike Latin letters
(``Встреча`` comes back as ``BcTpe4a``), and translating that produces fluent
nonsense. A recognised-but-wrong transcription is worse than a failed one, so each
language is mapped to a recognition model that can actually read it, and the Cyrillic
model is downloaded on demand (see :data:`SCRIPT_MODELS`).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .config import OcrSettings
from .wordjoin import restore_spaces

_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff00-\uffef]")
_WS = re.compile(r"[ \t\u00a0]+")

#: Recognition models for writing systems the bundled Chinese+Latin model cannot read.
#: Each entry is a directory under ``models/ocr/`` holding ``model.onnx`` and
#: ``dict.txt``. Only downloaded ones are used; if a model is absent the app falls
#: back to the bundled one and says so, rather than silently mistranscribing.
SCRIPT_MODELS = {
    "cyrillic": "cyrillic_PP-OCRv3_rec",
}

#: Which script each language is written in. Anything absent is Latin/CJK, which the
#: bundled model already covers.
LANG_SCRIPT = {
    "ru": "cyrillic",
    "uk": "cyrillic",
    "be": "cyrillic",
    "bg": "cyrillic",
    "sr": "cyrillic",
    "mk": "cyrillic",
    "kk": "cyrillic",
    "ky": "cyrillic",
    "mn": "cyrillic",
}

DEFAULT_SCRIPT = "latin"


def script_for(lang: str) -> str:
    return LANG_SCRIPT.get((lang or "").strip().lower(), DEFAULT_SCRIPT)


#: Recognition models that ship inside the package rather than being downloaded. The
#: Cyrillic one is 8.6 MB, and without it a Russian document cannot be read at all - which
#: is the one thing this program exists to do. Making it a download would put a cliff in
#: front of the first thing a new user tries.
BUNDLED_OCR_DIR = Path(__file__).resolve().parent / "data" / "ocr"


def script_model_dir(models_root: str | Path | None, script: str) -> Path | None:
    """The on-disk directory for a script's model, or None if it is not installed.

    models/ is checked first, so a user can drop a better model in without touching the
    installed package. The bundled copy is the fallback that always works.
    """
    name = SCRIPT_MODELS.get(script)
    if not name:
        return None
    candidates: list[Path] = []
    if models_root:
        candidates.append(Path(models_root) / "ocr" / name)
    candidates.append(BUNDLED_OCR_DIR / name)
    for directory in candidates:
        if (directory / "model.onnx").is_file() and (directory / "dict.txt").is_file():
            return directory
    return None


def script_available(models_root: str | Path | None, script: str) -> bool:
    return script == DEFAULT_SCRIPT or script_model_dir(models_root, script) is not None


#: Latin letters that are pixel-identical to a Cyrillic letter. PP-OCR's Cyrillic
#: dictionary contains *both* alphabets, so it picks whichever fits the pixels and a
#: line comes back mixed: "Встреча" -> "Bстрeча". Half-Cyrillic text translates to
#: nonsense, so the lookalikes are folded back once the language is known.
_LATIN_TO_CYRILLIC = {
    "A": "А", "B": "В", "C": "С", "E": "Е", "H": "Н", "I": "І", "K": "К",
    "M": "М", "O": "О", "P": "Р", "S": "Ѕ", "T": "Т", "X": "Х", "Y": "У",
    "a": "а", "c": "с", "e": "е", "o": "о", "p": "р", "s": "ѕ", "x": "х",
    "y": "у", "u": "и", "n": "п", "k": "к", "m": "м", "t": "т", "r": "г",
    "b": "ь", "d": "д", "g": "г", "h": "н", "i": "і", "j": "ј", "l": "л",
    "v": "в", "w": "ш", "z": "з",
}

#: Digits the Cyrillic model happily emits for letters that look like them.
_DIGIT_TO_CYRILLIC = {"3": "з", "4": "ч", "6": "б", "0": "о", "9": "д"}

_CYRILLIC_LETTER = re.compile(r"[Ѐ-ӿ]")
#: A digit becomes a letter only when a letter is next to it, so "100 тонн" stays a
#: number while "3автра" and "4aca" are repaired.
_DIGIT_NEAR_LETTER = re.compile(r"(?<=[Ѐ-ӿ])([304690])|([304690])(?=[Ѐ-ӿ])")

#: The reverse of the map above, and the reason it has to be careful.
#:
#: A trade document is made of numbers, and the Cyrillic model also emits *letters* where
#: the text had digits: "зо календарных дней" is "30 календарных дней". Losing that number
#: loses the sentence - measured, the translation came back as "付款之日起历日的交付期"
#: with the quantity simply gone.
#:
#: Unlike the other direction this cannot be a blanket swap: "до" (until) and "об" (about)
#: are ordinary Russian words spelled entirely in lookalike letters, and reading them as
#: "90" and "06" would wreck far more text than it repairs.
#:
#: The word list separates the two, but only by score, not by membership - "зо" and "чо"
#: are in the list as corpus debris. Measured over the two-letter entries:
#:
#:     real words    до 6.38, об 5.96, он 6.59, не 7.20, на 7.25 ... 5.75 and up
#:     misreads      зо 3.27, чо 4.02, бо 4.19, зб 2.53, дд 2.99 ... 2.18 to 4.67
#:
#: Nothing lands between 4.67 and 5.75, so that is where the line goes.
#:
#: "ООО" scores 4.67 - on the wrong side of it - and is a legal form that appears in every
#: Russian contract. It is saved by being capitalised: a number is never written in
#: capitals, an abbreviation always is.
_CYRILLIC_TO_DIGIT = {"о": "0", "О": "0", "з": "3", "З": "3", "б": "6", "Б": "6",
                      "ч": "4", "Ч": "4", "д": "9", "Д": "9"}

#: Below this, a token is corpus debris rather than a word. See the table above.
_WORD_SCORE_FLOOR = 5.0

#: A standalone token short enough to be a number, mixing lookalike letters and digits
#: ("зо", "б5"). Bounded on both sides so it never bites into a longer word.
_LOOKALIKE_TOKEN = re.compile(r"(?<![\wЀ-ӿ])([Ѐ-ӿ\d]{2,5})(?![\wЀ-ӿ])")


#: The confusions the recogniser was measured to make, not the ones that look plausible on
#: paper. Twenty rendered sentences of trade Russian produced, apart from case and dropped
#: punctuation, exactly three kinds of letter error: ":" read as "л" at the end of a word,
#: "щ" read as "ц", and a capital "Ц" dropped from the front of a word.
#:
#: The repairs below are tried only on a token that is *not* a Russian word, and only
#: accepted when exactly one candidate *is* a word. Measured on those twenty sentences:
#: three lines repaired, none made worse.
_CONFUSIONS = [("ц", "щ"), ("щ", "ц"), ("ч", "щ"), ("ш", "щ"), ("ь", "ъ"), ("ъ", "ь"),
               ("ь", "б"), ("б", "ь"), ("ч", "у"), ("у", "ч")]
#: Read where a colon or a hyphen was.
_TRAILING_NOISE = "л"
#: A word-initial capital the recogniser swallowed whole: "Оплата" arrives as "плата",
#: "Гарантия" as "арантия", "Пришлите" as "ришлите". Any letter may be the missing one, so
#: all of them are tried and the "exactly one" rule decides. Measured on those three:
#: "арантия" and "ришлите" have exactly one completion each, and the tokens that really
#: were mangled beyond recovery have none - so nothing is invented.
_HEAD_LETTERS = "абвгдежзийклмнопрстуфхцчшщыэюя"


def _word_candidates(lowered: str) -> list[str]:
    out: list[str] = []
    for index, ch in enumerate(lowered):
        for a, b in _CONFUSIONS:
            if ch == a:
                out.append(lowered[:index] + b + lowered[index + 1:])
    if len(lowered) > 3 and lowered.endswith(_TRAILING_NOISE):
        out.append(lowered[:-1])
    if len(lowered) > 3:
        out.extend(letter + lowered for letter in _HEAD_LETTERS)
    # A letter the recogniser saw twice: "соответствует" arrived as "соответтствует".
    for index in range(len(lowered) - 1):
        if lowered[index] == lowered[index + 1]:
            out.append(lowered[:index] + lowered[index + 1:])
    # A Latin capital at the head of a Cyrillic word - "Цена" arrives as "Lена".
    if lowered[:1].isascii() and lowered[:1].isalpha():
        out.extend(letter + lowered[1:] for letter in _HEAD_LETTERS)
    return out


def repair_words(text: str, words: dict | None = None) -> str:
    """Fix a token the recogniser mangled, when the word list says what it meant.

    Deliberately conservative: an unknown token is left alone unless one - and only one -
    small edit turns it into a known word. An ambiguous token is left as recognised, since
    guessing wrong would replace a wrong word with a different wrong word.
    """
    if not text:
        return text
    if words is None:
        try:
            from .wordjoin import load_words

            words = load_words()
        except Exception:  # noqa: BLE001
            words = {}
    if not words:
        return text

    out: list[str] = []
    for token in text.split(" "):
        head = ""
        tail = ""
        core = token
        while core and not core[0].isalpha():
            head += core[0]
            core = core[1:]
        while core and not core[-1].isalpha():
            tail = core[-1] + tail
            core = core[:-1]
        if not core or core.lower() in words:
            out.append(token)
            continue
        hits = [c for c in _word_candidates(core.lower()) if c in words]
        if len(hits) != 1:
            out.append(token)
            continue
        # Candidates are built lowercase, so put the capital back: "Обцая" becomes "Общая",
        # not "общая". A sentence-initial word that comes back lowercase is still wrong,
        # just less wrong than the word it was.
        fixed = hits[0]
        if core[:1].isupper():
            fixed = fixed[:1].upper() + fixed[1:]
        out.append(head + fixed + tail)
    return " ".join(out)


def repair_digits(text: str, words: dict | None = None) -> str:
    """Put back numbers the Cyrillic model spelled as letters.

    A token is converted only when every character of it is a digit lookalike, it is not
    written in capitals, and the word list either lacks it or barely knows it.
    """
    if not text or not _LOOKALIKE_TOKEN.search(text):
        return text
    if words is None:
        try:
            from .wordjoin import load_words

            words = load_words()
        except Exception:  # noqa: BLE001
            words = {}

    def replace(match: re.Match) -> str:
        token = match.group(1)
        if token.isupper():
            return token
        digits = "".join(_CYRILLIC_TO_DIGIT.get(ch, ch if ch.isdigit() else "")
                         for ch in token)
        if len(digits) != len(token) or not any(ch.isdigit() for ch in digits):
            return token
        score = words.get(token.lower()) if words else None
        if score is not None and score >= _WORD_SCORE_FLOOR:
            return token
        return digits

    return _LOOKALIKE_TOKEN.sub(replace, text)


#: A capital inside a Russian word is impossible, and the recogniser produces them
#: because Latin "H"/"T"/"O" are the capitals that match lowercase Cyrillic "н"/"т"/"о".
#: Only letters *between* other letters are lowered, so word-initial capitals - which
#: may be genuine proper nouns - are left alone.
_MIDWORD_CAPITAL = re.compile(r"(?<=[Ѐ-ӿ])([Ѐ-ӿ])")


def fold_to_cyrillic(text: str) -> str:
    """Repair a Cyrillic line the recogniser mixed with lookalike Latin and digits."""
    if not text or not _CYRILLIC_LETTER.search(text):
        return text
    folded = "".join(_LATIN_TO_CYRILLIC.get(ch, ch) for ch in text)

    def repair(match: re.Match) -> str:
        return _DIGIT_TO_CYRILLIC.get(match.group(0), match.group(0))

    folded = _DIGIT_NEAR_LETTER.sub(repair, folded)
    # A capital *between* letters is always wrong ("указаНа" -> "указана").
    return _MIDWORD_CAPITAL.sub(lambda m: m.group(1).lower(), folded)


@dataclass
class OcrLine:
    text: str
    score: float
    box: list = field(default_factory=list)
    top: float = 0.0
    left: float = 0.0


@dataclass
class OcrResult:
    lines: list[OcrLine]
    text: str
    elapsed: float
    #: How many horizontal strips reused their previous reading (0 for a full read).
    reused_strips: int = 0

    @property
    def empty(self) -> bool:
        return not self.text.strip()


def is_cjk(ch: str) -> bool:
    return bool(_CJK.match(ch))


def join_lines(parts: list[str]) -> str:
    """Join recognised lines, omitting the space where CJK meets CJK."""
    out = ""
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if not out:
            out = part
            continue
        if is_cjk(out[-1]) and is_cjk(part[0]):
            out += part
        else:
            out += " " + part
    return out


def normalize(text: str) -> str:
    """Collapse whitespace and drop line breaks so cache keys stay stable."""
    if not text:
        return ""
    text = text.replace("\r", "\n")
    text = _WS.sub(" ", text)
    text = re.sub(r"\s*\n\s*", " ", text)
    return text.strip()


class OcrEngine:
    """Thin wrapper around :class:`rapidocr_onnxruntime.RapidOCR`."""

    def __init__(self, settings: OcrSettings | None = None,
                 models_root: str | Path | None = None,
                 script: str = DEFAULT_SCRIPT) -> None:
        self.settings = settings or OcrSettings()
        self.models_root = models_root
        self.script = script or DEFAULT_SCRIPT
        self._engine = None
        self._load_error: str | None = None
        self.load_seconds = 0.0
        self.last_seconds = 0.0
        #: Per-strip reuse cache for recognize_cached(): [{"signature", "lines"}].
        self._strip_cache: list[dict] = []
        #: Frame size the strip edges were computed for, and the edges themselves.
        self._strip_size: tuple[int, int] | None = None
        self._strip_edges: list[int] | None = None

    @property
    def uses_script_model(self) -> bool:
        """True when a non-default recognition model is actually in use."""
        return script_model_dir(self.models_root, self.script) is not None

    # -- lifecycle ---------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._engine is not None

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def load(self) -> None:
        """Build the ONNX sessions. Safe to call more than once."""
        if self._engine is not None:
            return
        started = time.perf_counter()
        try:
            from rapidocr_onnxruntime import RapidOCR
        except Exception as exc:  # noqa: BLE001
            self._load_error = f"无法导入 RapidOCR：{exc}"
            raise RuntimeError(self._load_error) from exc

        kwargs = {
            "text_score": float(self.settings.min_score),
            "use_cls": False,
            "width_height_ratio": -1,
            "max_side_len": 1600,
            "min_side_len": 12,
            # Override the detector's upscaling default; see module docstring.
            "det_limit_type": "max",
            "det_limit_side_len": int(self.settings.max_side_len),
        }
        if self.settings.intra_op_threads and self.settings.intra_op_threads > 0:
            kwargs["intra_op_num_threads"] = int(self.settings.intra_op_threads)

        # Swap in a recognition model that can read this language's script.
        # UpdateParameters() strips the "rec_" prefix from rec_model_path, while
        # rec_keys_path is passed through untouched - which is exactly the key
        # TextRecognizer looks up for its character dictionary.
        model_dir = script_model_dir(self.models_root, self.script)
        if model_dir is not None:
            kwargs["rec_model_path"] = str(model_dir / "model.onnx")
            kwargs["rec_keys_path"] = str(model_dir / "dict.txt")

        try:
            self._engine = RapidOCR(**kwargs)
        except Exception as exc:  # noqa: BLE001
            if model_dir is not None:
                # A broken optional model must not take the app down with it.
                self._load_error = (
                    f"加载 {self.script} 识别模型失败（{exc}），已改回内置中英文模型")
                kwargs.pop("rec_model_path", None)
                kwargs.pop("rec_keys_path", None)
                try:
                    self._engine = RapidOCR(**kwargs)
                except Exception as exc2:  # noqa: BLE001
                    self._load_error = f"初始化 OCR 引擎失败：{exc2}"
                    raise RuntimeError(self._load_error) from exc2
            else:
                self._load_error = f"初始化 OCR 引擎失败：{exc}"
                raise RuntimeError(self._load_error) from exc
        self.load_seconds = time.perf_counter() - started

    def warmup(self) -> None:
        """Run one throwaway inference so the first real frame is not slow."""
        self.load()
        blank = np.zeros((48, 320, 3), dtype=np.uint8)
        try:
            self.recognize(blank)
        except Exception:  # noqa: BLE001
            pass

    # -- incremental recognition ------------------------------------------
    #: How many horizontal strips a frame is split into. Three isolates "the bottom of a
    #: chat changed" without multiplying the detector's own overhead.
    STRIP_COUNT = 3
    #: A boundary must sit inside a band of rows this wide with no strong vertical edges,
    #: otherwise a text line gets cut in half and read as two broken halves.
    BOUNDARY_BAND = 8
    #: How far to look for that blank band around the ideal boundary.
    BOUNDARY_SEARCH = 40
    #: Fraction of sampled pixels that may differ and still count as the same strip.
    STRIP_TOLERANCE = 0.004

    def recognize_cached(self, bgr: np.ndarray) -> OcrResult:
        """Recognise a frame, re-running only the strips that changed.

        One detection pass over the whole frame, then recognition only for the crops that
        belong to a strip whose pixels moved. Measured here: detection 1.3 s and recognition
        6 s for 47 boxes at 128 ms each, so skipping most of the recognition is the win.

        Detecting once also removes the risk the earlier strip version carried: crops come
        from the whole-frame detector, so a text line that happens to straddle a strip
        boundary is still read in one piece.

        Falls back to a plain full read when anything looks unusual. A slow correct answer
        beats a fast wrong one.
        """
        if self._engine is None:
            self.load()

        started = time.perf_counter()
        height, width = bgr.shape[:2]
        if height < self.STRIP_COUNT * 40:
            self._strip_cache = []
            return self.recognize(bgr)

        try:
            img = self._engine.load_img(bgr)
            det = self._engine.text_det(img)
            boxes = det[0] if isinstance(det, tuple) else det
        except Exception:  # noqa: BLE001
            self._strip_cache = []
            return self.recognize(bgr)
        if boxes is None or len(boxes) == 0:
            self._strip_cache = []
            return OcrResult(lines=[], text="", elapsed=time.perf_counter() - started)

        boxes = self._engine.sorted_boxes(boxes)
        crops = self._engine.get_crop_img_list(img, boxes)
        if not crops:
            self._strip_cache = []
            return self.recognize(bgr)

        step = height / self.STRIP_COUNT
        #: For each strip: did it change, and the raw text of its crops.
        changed: list[bool] = []
        buckets: list[list[int]] = [[] for _ in range(self.STRIP_COUNT)]
        for index, box in enumerate(boxes):
            points = np.asarray(box)
            centre = float(points[:, 1].mean())
            slot = min(self.STRIP_COUNT - 1, max(0, int(centre // step)))
            buckets[slot].append(index)
        for slot in range(self.STRIP_COUNT):
            top = int(round(slot * step))
            bottom = int(round((slot + 1) * step)) if slot + 1 < self.STRIP_COUNT else height
            signature = bgr[top:bottom][::4, ::4, :3].astype(np.int16)
            known = self._strip_cache[slot]["signature"] if slot < len(self._strip_cache) else None
            if known is not None and known.shape == signature.shape:
                moved = float((np.abs(known - signature) > 12).mean()) >= self.STRIP_TOLERANCE
            else:
                moved = True
            changed.append(moved)
            if not changed[slot]:
                continue

        wanted = [i for slot in range(self.STRIP_COUNT) if changed[slot] for i in buckets[slot]]
        texts: dict[int, tuple[str, float]] = {}
        if wanted:
            try:
                rec = self._engine.text_rec([crops[i] for i in wanted])
                rec = rec[0] if isinstance(rec, tuple) else rec
            except Exception:  # noqa: BLE001
                self._strip_cache = []
                return self.recognize(bgr)
            for index, item in zip(wanted, rec or []):
                texts[index] = (str(item[0]), float(item[1]))

        lines: list[OcrLine] = []
        cache: list[dict] = []
        for slot in range(self.STRIP_COUNT):
            if changed[slot]:
                slot_lines = []
                for index in buckets[slot]:
                    if index not in texts:
                        continue
                    text, score = texts[index]
                    text = self._clean(text)
                    box = boxes[index]
                    points = np.asarray(box)
                    top = float(points[:, 1].min())
                    left = float(points[:, 0].min())
                    slot_lines.append(OcrLine(text=text, score=score, box=box,
                                              top=top, left=left))
                signature = bgr[int(round(slot * step)):
                                (int(round((slot + 1) * step))
                                 if slot + 1 < self.STRIP_COUNT else height)][::4, ::4, :3]
                cache.append({"signature": signature.astype(np.int16), "lines": slot_lines})
                lines.extend(slot_lines)
            else:
                previous = self._strip_cache[slot]
                cache.append(previous)
                lines.extend(previous["lines"])

        self._strip_cache = cache
        lines.sort(key=lambda ln: (round(ln.top / 8), ln.left))
        return OcrResult(lines=lines, text=join_lines([ln.text for ln in lines]),
                         elapsed=time.perf_counter() - started,
                         reused_strips=sum(1 for c in changed if not c))

    def _clean(self, text: str) -> str:
        """Normalise one recognised run of text.

        Shared by the plain and the incremental path so the two cannot drift apart - the
        cyrillic repairs are what make the Russian model's output usable at all, and a
        cached strip must go through exactly the same steps.
        """
        text = normalize(str(text))
        if not text:
            return ""
        if self.script == "cyrillic":
            text = fold_to_cyrillic(text)
            # Numbers first: restore_spaces ignores tokens containing digits, so it has to
            # see the real ones rather than letters standing in for them.
            text = repair_digits(text)
            # The Cyrillic model also drops spaces between short words; recover the
            # boundaries so the translator sees words instead of one long token.
            text = restore_spaces(text)
            # Last, because splitting is what turns a mangled run of letters back into
            # tokens that can be looked up one at a time.
            text = repair_words(text)
        return text

    # -- inference ---------------------------------------------------------
    def _infer(self, bgr: np.ndarray):
        """Detect once, then hand every crop to the recogniser in a single call.

        Measured honestly: this did NOT make it faster on RapidOCR 1.x. A 900x700 window
        with 42 text lines took ~7.2 s this way against ~7.1 s through __call__, because
        the recogniser loops over the crops internally either way. It is kept because the
        split is the only place the timing can be attributed - the detector is 0.9 s and
        the recogniser is everything else - and because a RapidOCR build that batches
        properly would make this pay off without touching the call site.

        Falls back to the one-shot call when this build has a different shape, because a
        slower correct result beats a fast crash.
        """
        engine = self._engine
        try:
            img = engine.load_img(bgr)
            det = engine.text_det(img)
            boxes = det[0] if isinstance(det, tuple) else det
            if boxes is None or len(boxes) == 0:
                return []
            boxes = engine.sorted_boxes(boxes)
            crops = engine.get_crop_img_list(img, boxes)
            if not crops:
                return []
            rec = engine.text_rec(crops)
            rec = rec[0] if isinstance(rec, tuple) else rec
            boxes, rec = engine.filter_result(boxes, rec)
            if boxes is None or rec is None:
                return []
            return [(box, item[0], item[1]) for box, item in zip(boxes, rec)]
        except Exception:  # noqa: BLE001
            raw, _elapse = engine(bgr)
            return raw or []

    def recognize(self, bgr: np.ndarray) -> OcrResult:
        if self._engine is None:
            self.load()
        started = time.perf_counter()
        try:
            raw = self._infer(bgr)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"OCR 推理失败：{exc}") from exc
        elapsed = time.perf_counter() - started
        self.last_seconds = elapsed

        lines: list[OcrLine] = []
        for item in raw or []:
            try:
                box, text, score = item[0], item[1], float(item[2])
            except Exception:  # noqa: BLE001
                continue
            text = self._clean(text)
            if not text:
                continue
            # box is a numpy array here, so "if box" is ambiguous - test the length.
            has_box = box is not None and len(box) > 0
            top = min(point[1] for point in box) if has_box else 0.0
            left = min(point[0] for point in box) if has_box else 0.0
            lines.append(OcrLine(text=text, score=score, box=box, top=top, left=left))

        lines.sort(key=lambda ln: (round(ln.top / 8), ln.left))
        return OcrResult(lines=lines, text=join_lines([ln.text for ln in lines]), elapsed=elapsed)
