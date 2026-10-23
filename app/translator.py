"""Offline translation with OPUS-MT (Marian) ONNX models.

The models are exported by Optimum, so the graph signatures are the standard ones:

* ``encoder_model.onnx``            -> ``input_ids``, ``attention_mask``
* ``decoder_model.onnx``            -> ``input_ids``, ``encoder_attention_mask``,
                                       ``encoder_hidden_states``; it emits both the
                                       decoder and the cross-attention (encoder) KV
* ``decoder_with_past_model.onnx``  -> ``input_ids``, ``encoder_attention_mask``,
                                       ``past_key_values.*``; it re-emits only the
                                       decoder KV, so the encoder KV has to be carried
                                       forward by the caller

Decoding is beam search, because the shipped generation config asks for
``num_beams=4`` and greedy output is measurably worse on short subtitle lines.
"""

from __future__ import annotations

import json
import re
import time
from collections import OrderedDict
from pathlib import Path

import numpy as np

from .config import TranslateSettings

_CJK = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
#: Characters that should hug each other with no space in between.
_JOINABLE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")


class TranslateError(RuntimeError):
    pass


def tidy_cjk(text: str) -> str:
    """Remove the spaces Marian inserts between Chinese characters."""
    if not text:
        return ""
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch == " ":
            prev = out[-1] if out else ""
            nxt = text[i + 1] if i + 1 < len(text) else ""
            if prev and nxt and _JOINABLE.match(prev) and _JOINABLE.match(nxt):
                continue
        out.append(ch)
    return "".join(out)


#: Half-width marks the model emits that should be full-width next to Chinese.
_PUNCT_MAP = {",": "，", "!": "！", "?": "？", ":": "：", ";": "；"}

#: A unit repeated three or more times in a row. Two is left alone so ordinary Chinese
#: reduplication (谢谢, 看看) survives.
_REPEATED_UNIT = re.compile(r"(.{1,12}?)\1{2,}")


#: Garbling the offline models produce often enough to fix rather than explain away.
#: Each entry is (pattern, replacement) on the TRANSLATED text and is deliberately narrow:
#: a general "collapse doubled characters" rule would eat real words like 谢谢 and 常常.
_OUTPUT_REPAIRS: tuple[tuple[str, str], ...] = (
    # "45 календарных дней" -> "45个历日": the second 日 goes missing.
    ("历日", "日历日"),
    # "склада покупателя" -> "买方方 ' 仓库": a stray character doubles the word.
    ("买方方", "买方"),
    ("卖方方", "卖方"),
    ("供应商商", "供应商"),
    # "соответствовать требованиям" -> "应合格…的要求": 符合 is the verb it wants.
    ("应合格", "应符合"),
    ("须合格", "须符合"),
)


def repair_output(text: str) -> str:
    """Fix the specific garbling the offline models produce, leaving everything else alone."""
    if not text:
        return text
    for pattern, replacement in _OUTPUT_REPAIRS:
        if pattern in text:
            text = text.replace(pattern, replacement)
    return text


def collapse_repeats(text: str) -> str:
    """Collapse degenerate repetition in the decoder's output.

    Asked for a single word the Marian model sometimes never emits end-of-sequence and
    loops on the same token: "таможня" produced "海关" hundreds of times. That fills the
    subtitle with one character and pushes the real output off screen. A loop is easy to
    recognise and safe to collapse, and normal text is untouched.
    """
    if not text or len(text) < 4:
        return text

    # The whole string being the same unit over and over.
    length = len(text)
    for size in range(1, length // 2 + 1):
        if length % size:
            continue
        unit = text[:size]
        if unit * (length // size) == text:
            return unit

    # A unit repeated inside a longer answer.
    return _REPEATED_UNIT.sub(r"\1", text)


def normalize_cjk_punctuation(text: str) -> str:
    """Turn half-width punctuation into full-width where it touches Chinese text.

    Marian output such as "你要明白,我从来没有..." reads wrong in Chinese; it should be
    "你要明白，我从来没有...". Decimal points and ellipses are left alone, and the rule
    never fires for Latin output because it requires an adjacent CJK character.
    """
    if not text:
        return ""
    out: list[str] = []
    for i, ch in enumerate(text):
        prev = text[i - 1] if i else ""
        nxt = text[i + 1] if i + 1 < len(text) else ""
        prev_cjk = bool(prev and _CJK.match(prev))
        next_cjk = bool(nxt and _CJK.match(nxt))

        if ch == ".":
            if prev.isdigit() and nxt.isdigit():      # 3.5
                out.append(ch)
            elif prev == "." or nxt == ".":           # ...
                out.append(ch)
            elif prev_cjk or next_cjk:
                out.append("。")
            else:
                out.append(ch)
            continue
        if ch in _PUNCT_MAP:
            out.append(_PUNCT_MAP[ch] if (prev_cjk or next_cjk) else ch)
            continue
        if ch == "(" and next_cjk:
            out.append("（")
            continue
        if ch == ")" and prev_cjk:
            out.append("）")
            continue
        out.append(ch)
    return "".join(out)


def _log_softmax(x: np.ndarray) -> np.ndarray:
    peak = np.max(x, axis=-1, keepdims=True)
    peak = np.where(np.isfinite(peak), peak, 0.0)
    shifted = x - peak
    return shifted - np.log(np.sum(np.exp(shifted), axis=-1, keepdims=True))


# -- shared ONNX plumbing ---------------------------------------------------
# Both engines drive the same Optimum export layout, so the session plumbing lives
# here rather than on either translator.
def build_decoder_feed(
    session,
    input_ids: np.ndarray,
    encoder_attention_mask: np.ndarray,
    encoder_hidden: np.ndarray | None,
    past: dict | None,
) -> dict:
    """Feed only the inputs the session actually declares.

    Merged exports additionally take a ``use_cache_branch`` flag and expect
    zero-length past tensors when the cache is not used yet.
    """
    feed: dict = {}
    for spec in session.get_inputs():
        name = spec.name
        if name == "input_ids":
            feed[name] = input_ids
        elif name == "encoder_attention_mask":
            feed[name] = encoder_attention_mask
        elif name == "encoder_hidden_states" and encoder_hidden is not None:
            feed[name] = encoder_hidden
        elif name == "use_cache_branch":
            feed[name] = np.array([past is not None])
        elif "past_key_values" in name:
            if past is not None and name in past:
                feed[name] = past[name]
            else:
                shape = [d if isinstance(d, int) else 1 for d in spec.shape]
                shape = [0 if idx == 2 else d for idx, d in enumerate(shape)]
                feed[name] = np.zeros(shape, dtype=np.float32)
    return feed


def collect_present(session, outputs) -> dict:
    """Map ``present.*`` outputs onto the ``past_key_values.*`` input names."""
    past = {}
    for spec, value in zip(session.get_outputs(), outputs):
        if spec.name.startswith("present"):
            past[spec.name.replace("present", "past_key_values", 1)] = value
    return past


def mask_tokens(logits: np.ndarray, tokens) -> np.ndarray:
    """Set the logits of *tokens* to -inf so they can never be selected."""
    for token in tokens:
        if 0 <= token < logits.shape[-1]:
            logits[..., token] = -np.inf
    return logits


class Translator:
    """Loads one language pair and translates text with it."""

    def __init__(self, model_dir: str, settings: TranslateSettings | None = None) -> None:
        self.model_dir = Path(model_dir)
        self.settings = settings or TranslateSettings()
        self._enc = None
        self._dec = None
        self._dec_past = None
        self._tokenizer = None
        self._cache: OrderedDict[str, str] = OrderedDict()

        self.start_token_id = 0
        self.eos_token_id = 0
        self.pad_token_id = 0
        self.vocab_size = 0
        self.max_length = int(self.settings.max_length)
        self.num_beams = 1
        self.length_penalty = float(self.settings.length_penalty or 1.0)
        self.bad_words: set[int] = set()
        self.load_seconds = 0.0
        self.last_seconds = 0.0
        self._io_note = ""

    # -- loading -----------------------------------------------------------
    def _model_path(self, *names: str) -> str:
        for name in names:
            candidate = self.model_dir / name
            if candidate.is_file():
                return str(candidate)
        raise TranslateError(f"模型文件缺失：{names[0]}（{self.model_dir}）")

    def load(self) -> None:
        if self._enc is not None:
            return
        started = time.perf_counter()
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        providers = ["CPUExecutionProvider"]

        encoder_path = self._model_path(
            "onnx/encoder_model_quantized.onnx", "onnx/encoder_model.onnx"
        )
        decoder_path = self._model_path(
            "onnx/decoder_model_quantized.onnx", "onnx/decoder_model.onnx"
        )
        decoder_past_path = self._model_path(
            "onnx/decoder_with_past_model_quantized.onnx",
            "onnx/decoder_with_past_model.onnx",
        )

        try:
            self._enc = ort.InferenceSession(encoder_path, options, providers=providers)
            self._dec = ort.InferenceSession(decoder_path, options, providers=providers)
            self._dec_past = ort.InferenceSession(decoder_past_path, options, providers=providers)
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"加载 ONNX 模型失败：{exc}") from exc

        self._load_tokenizer()
        self._load_generation_config()

        if any("past_key_values" in i.name for i in self._dec.get_inputs()):
            self._io_note = "decoder_model 含 past 输入，已按空历史处理"
        self.load_seconds = time.perf_counter() - started

    def _load_tokenizer(self) -> None:
        try:
            from transformers import AutoTokenizer
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"无法导入 transformers：{exc}") from exc
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(self.model_dir), local_files_only=True
            )
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"加载分词器失败：{exc}") from exc

    def _load_generation_config(self) -> None:
        cfg: dict = {}
        gen: dict = {}
        for name, target in (("config.json", cfg), ("generation_config.json", gen)):
            path = self.model_dir / name
            if path.is_file():
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        target.update(json.load(fh))
                except Exception:  # noqa: BLE001
                    pass

        def pick(key: str, default):
            for source in (gen, cfg):
                if source.get(key) is not None:
                    return source[key]
            return default

        self.vocab_size = int(pick("vocab_size", 0) or 0)
        self.pad_token_id = int(pick("pad_token_id", 0) or 0)
        self.eos_token_id = int(pick("eos_token_id", 0) or 0)
        self.start_token_id = int(pick("decoder_start_token_id", self.pad_token_id) or 0)
        self.max_length = int(self.settings.max_length or pick("max_length", 256))

        model_beams = int(pick("num_beams", 1) or 1)
        self.model_default_beams = max(1, model_beams)
        self.set_beams(self.settings.num_beams)
        self.length_penalty = float(self.settings.length_penalty or pick("length_penalty", 1.0) or 1.0)

        for group in pick("bad_words_ids", []) or []:
            for token in group or []:
                try:
                    self.bad_words.add(int(token))
                except (TypeError, ValueError):
                    continue

    # -- inference ---------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._enc is not None

    def set_beams(self, requested: int | None) -> None:
        """0 / None keeps the beam count the model's own config recommends."""
        try:
            value = int(requested or 0)
        except (TypeError, ValueError):
            value = 0
        self.num_beams = max(1, value) if value > 0 else getattr(self, "model_default_beams", 1)

    def _encode(self, input_ids: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        names = {i.name for i in self._enc.get_inputs()}
        feed = {}
        for name in names:
            if "input_ids" in name:
                feed[name] = input_ids
            elif "attention_mask" in name:
                feed[name] = attention_mask
        if not feed:
            feed = {"input_ids": input_ids, "attention_mask": attention_mask}
        return self._enc.run(None, feed)[0]

    def _decoder_feed(
        self,
        session,
        input_ids: np.ndarray,
        encoder_attention_mask: np.ndarray,
        encoder_hidden: np.ndarray | None,
        past: dict | None,
    ) -> dict:
        return build_decoder_feed(session, input_ids, encoder_attention_mask,
                                  encoder_hidden, past)

    @staticmethod
    def _collect_present(session, outputs) -> dict:
        return collect_present(session, outputs)

    def _mask_bad_words(self, logits: np.ndarray) -> np.ndarray:
        return mask_tokens(logits, self.bad_words) if self.bad_words else logits

    def translate(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached

        started = time.perf_counter()
        result = self._translate_uncached(text)
        self.last_seconds = time.perf_counter() - started

        self._cache[text] = result
        while len(self._cache) > max(16, int(self.settings.cache_size)):
            self._cache.popitem(last=False)
        return result

    def _translate_uncached(self, text: str) -> str:
        if self._enc is None:
            self.load()
        assert self._tokenizer is not None

        try:
            batch = self._tokenizer([text], return_tensors="np", padding=True)
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"分词失败：{exc}") from exc

        input_ids = np.asarray(batch["input_ids"], dtype=np.int64)
        attention_mask = np.asarray(batch["attention_mask"], dtype=np.int64)
        try:
            hidden = self._encode(input_ids, attention_mask)
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"编码失败：{exc}") from exc

        max_new = max(8, min(self.max_length, 512))
        try:
            ids = self._search(hidden, attention_mask, max_new)
        except TranslateError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"解码失败：{exc}") from exc

        if not ids:
            return ""
        try:
            decoded = self._tokenizer.decode(ids, skip_special_tokens=True)
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"还原文本失败：{exc}") from exc
        decoded = decoded.strip()
        if self.settings.join_cjk:
            decoded = collapse_repeats(tidy_cjk(decoded))
        if self.settings.normalize_punctuation:
            decoded = normalize_cjk_punctuation(decoded)
        return decoded

    def _search(self, hidden: np.ndarray, attention_mask: np.ndarray, max_new: int) -> list[int]:
        """Beam search; degenerates to greedy when ``num_beams == 1``."""
        beams = max(1, self.num_beams)

        start = np.array([[self.start_token_id]], dtype=np.int64)
        outputs = self._dec.run(
            None,
            self._decoder_feed(self._dec, start, attention_mask, hidden, None),
        )
        logits = self._mask_bad_words(outputs[0][0, -1, :].astype(np.float32, copy=True))
        logprobs = _log_softmax(logits)
        vocab = logprobs.shape[-1]
        keep = max(1, min(beams, vocab))
        top = np.argpartition(-logprobs, keep - 1)[:keep]
        top = top[np.argsort(-logprobs[top])]

        base_past = self._collect_present(self._dec, outputs)
        finished: list[tuple[float, list[int]]] = []
        seqs: list[list[int]] = []
        scores: list[float] = []
        currents: list[int] = []
        for token in top:
            token = int(token)
            if token == self.eos_token_id:
                finished.append((float(logprobs[token]), [token]))
                continue
            seqs.append([token])
            scores.append(float(logprobs[token]))
            currents.append(token)

        past: dict | None = None
        step = 1
        while seqs and step < max_new:
            count = len(seqs)
            if past is None:
                past = {name: np.repeat(value, count, axis=0) for name, value in base_past.items()}
            mask = np.repeat(attention_mask, count, axis=0)
            current = np.array([[token] for token in currents], dtype=np.int64)
            outputs = self._dec_past.run(
                None, self._decoder_feed(self._dec_past, current, mask, None, past)
            )
            logits = self._mask_bad_words(outputs[0][:, -1, :].astype(np.float32, copy=True))
            logprobs = _log_softmax(logits)

            candidates = np.asarray(scores, dtype=np.float32)[:, None] + logprobs
            flat = candidates.reshape(-1)
            keep = min(count, flat.size)
            top = np.argpartition(-flat, keep - 1)[:keep]
            top = top[np.argsort(-flat[top])]

            present = self._collect_present(self._dec_past, outputs)
            merged = dict(past)
            merged.update(present)  # decoder KV refreshed, encoder KV carried over

            next_seqs: list[list[int]] = []
            next_scores: list[float] = []
            next_currents: list[int] = []
            next_past: dict[str, list[np.ndarray]] = {name: [] for name in merged}

            for rank in top:
                rank = int(rank)
                beam_index, token = divmod(rank, vocab)
                sequence = seqs[beam_index] + [token]
                if token == self.eos_token_id:
                    finished.append((float(flat[rank]), sequence))
                    continue
                next_seqs.append(sequence)
                next_scores.append(float(flat[rank]))
                next_currents.append(token)
                for name, value in merged.items():
                    next_past[name].append(value[beam_index])

            if not next_seqs:
                past = None
                break
            past = {name: np.stack(values, axis=0) for name, values in next_past.items()}
            seqs, scores, currents = next_seqs, next_scores, next_currents
            step += 1

        pool = list(finished)
        pool.extend(zip(scores, seqs))
        if not pool:
            return []
        best = max(
            pool,
            key=lambda item: item[0] / (max(1, len(item[1])) ** self.length_penalty),
        )
        return [token for token in best[1] if token != self.eos_token_id]

    # -- diagnostics -------------------------------------------------------
    def describe(self) -> str:
        if self._enc is None:
            return "模型未加载"
        lines = [
            f"encoder inputs : {[i.name for i in self._enc.get_inputs()]}",
            f"decoder inputs : {[i.name for i in self._dec.get_inputs()]}",
            f"past inputs    : {len(self._dec_past.get_inputs())} tensors",
            f"start/eos/pad  : {self.start_token_id}/{self.eos_token_id}/{self.pad_token_id}",
            f"beams/penalty  : {self.num_beams}/{self.length_penalty}",
            f"bad words      : {sorted(self.bad_words)}",
        ]
        if self._io_note:
            lines.append(f"note           : {self._io_note}")
        return "\n".join(lines)


class NllbTranslator:
    """NLLB-200 / M2M-100 style decoding.

    Two things differ from Marian:

    * the source language has to be set on the tokenizer, which then prepends the
      matching language token to the encoder input;
    * the target language is imposed as the *first generated token* rather than being
      chosen by the model (``forced_bos_token_id`` in Hugging Face terms).

    Decoding is greedy. Beam search would need the forced token to seed every beam,
    and greedy already measured as the safer choice for this app.
    """

    def __init__(self, model_dir: str, src_lang: str, tgt_lang: str,
                 settings: TranslateSettings | None = None) -> None:
        self.model_dir = Path(model_dir)
        self.src_lang = src_lang
        self.tgt_lang = tgt_lang
        self.settings = settings or TranslateSettings()
        self._enc = None
        self._dec = None
        self._dec_past = None
        self._tokenizer = None
        self._cache: OrderedDict[str, str] = OrderedDict()

        self.start_token_id = 2
        self.eos_token_id = 2
        self.pad_token_id = 1
        self.forced_first_token: int | None = None
        self.bad_words: set[int] = set()
        self.num_beams = 1
        self.model_default_beams = 1
        self.max_length = int(self.settings.max_length)
        self.load_seconds = 0.0
        self.last_seconds = 0.0

    # -- loading -----------------------------------------------------------
    def load(self) -> None:
        if self._enc is not None:
            return
        started = time.perf_counter()
        import onnxruntime as ort

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        options.log_severity_level = 3
        providers = ["CPUExecutionProvider"]

        def path(name: str) -> str:
            candidate = self.model_dir / name
            if not candidate.is_file():
                raise TranslateError(f"模型文件缺失：{name}（{self.model_dir}）")
            return str(candidate)

        try:
            self._enc = ort.InferenceSession(path("onnx/encoder_model_quantized.onnx"),
                                             options, providers=providers)
            self._dec = ort.InferenceSession(path("onnx/decoder_model_quantized.onnx"),
                                             options, providers=providers)
            self._dec_past = ort.InferenceSession(
                path("onnx/decoder_with_past_model_quantized.onnx"), options, providers=providers)
        except TranslateError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"加载 ONNX 模型失败：{exc}") from exc

        try:
            from transformers import AutoTokenizer
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"无法导入 transformers：{exc}") from exc
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(
                str(self.model_dir), local_files_only=True)
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"加载分词器失败：{exc}") from exc

        self._load_generation_config()
        try:
            self._tokenizer.src_lang = self.src_lang
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"设置源语言 {self.src_lang} 失败：{exc}") from exc
        try:
            token = self._tokenizer.convert_tokens_to_ids(self.tgt_lang)
        except Exception:  # noqa: BLE001
            token = None
        # convert_tokens_to_ids returns the unk id for an unknown token rather than
        # -1, so comparing against unk_token_id is what actually catches a typo.
        unk = getattr(self._tokenizer, "unk_token_id", None)
        if token is None or (unk is not None and int(token) == int(unk)):
            raise TranslateError(f"模型不认识目标语言代码 {self.tgt_lang}")
        self.forced_first_token = int(token)
        self.load_seconds = time.perf_counter() - started

    def _load_generation_config(self) -> None:
        cfg: dict = {}
        gen: dict = {}
        for name, target in (("config.json", cfg), ("generation_config.json", gen)):
            path = self.model_dir / name
            if path.is_file():
                try:
                    with open(path, "r", encoding="utf-8") as fh:
                        target.update(json.load(fh))
                except Exception:  # noqa: BLE001
                    pass

        def pick(key: str, default):
            for source in (gen, cfg):
                if source.get(key) is not None:
                    return source[key]
            return default

        self.start_token_id = int(pick("decoder_start_token_id", 2) or 2)
        self.eos_token_id = int(pick("eos_token_id", 2) or 2)
        self.pad_token_id = int(pick("pad_token_id", 1) or 1)
        self.max_length = int(self.settings.max_length or pick("max_length", 200))
        # NLLB pads with pad_token_id; letting it be generated corrupts short outputs.
        self.bad_words = {self.pad_token_id}
        if self.eos_token_id != self.pad_token_id:
            self.bad_words.discard(self.eos_token_id)

    # -- inference ---------------------------------------------------------
    @property
    def ready(self) -> bool:
        return self._enc is not None

    def set_beams(self, _requested: int | None) -> None:
        """NLLB is greedy-only here; kept so callers can treat engines uniformly."""
        self.num_beams = 1

    def _encode(self, input_ids: np.ndarray, attention_mask: np.ndarray) -> np.ndarray:
        names = {i.name for i in self._enc.get_inputs()}
        feed = {}
        for name in names:
            if "input_ids" in name:
                feed[name] = input_ids
            elif "attention_mask" in name:
                feed[name] = attention_mask
        if not feed:
            feed = {"input_ids": input_ids, "attention_mask": attention_mask}
        return self._enc.run(None, feed)[0]

    def translate(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached

        started = time.perf_counter()
        result = self._translate_uncached(text)
        self.last_seconds = time.perf_counter() - started

        self._cache[text] = result
        while len(self._cache) > max(16, int(self.settings.cache_size)):
            self._cache.popitem(last=False)
        return result

    def _translate_uncached(self, text: str) -> str:
        if self._enc is None:
            self.load()
        assert self._tokenizer is not None

        try:
            batch = self._tokenizer([text], return_tensors="np", padding=True)
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"分词失败：{exc}") from exc

        input_ids = np.asarray(batch["input_ids"], dtype=np.int64)
        attention_mask = np.asarray(batch["attention_mask"], dtype=np.int64)
        try:
            hidden = self._encode(input_ids, attention_mask)
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"编码失败：{exc}") from exc

        max_new = max(8, min(self.max_length, 400))
        try:
            ids = self._decode(hidden, attention_mask, max_new)
        except TranslateError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"解码失败：{exc}") from exc

        if not ids:
            return ""
        try:
            decoded = self._tokenizer.decode(ids, skip_special_tokens=True)
        except Exception as exc:  # noqa: BLE001
            raise TranslateError(f"还原文本失败：{exc}") from exc
        decoded = decoded.strip()
        if self.settings.join_cjk:
            decoded = collapse_repeats(tidy_cjk(decoded))
        if self.settings.normalize_punctuation:
            decoded = normalize_cjk_punctuation(decoded)
        return decoded

    def _decode(self, hidden: np.ndarray, attention_mask: np.ndarray, max_new: int) -> list[int]:
        start = np.array([[self.start_token_id]], dtype=np.int64)
        outputs = self._dec.run(
            None, build_decoder_feed(self._dec, start, attention_mask, hidden, None))
        # The step-0 logits are deliberately ignored: the first token is imposed.
        past = collect_present(self._dec, outputs)

        forced = self.forced_first_token
        if forced is None:
            logits = mask_tokens(outputs[0][0, -1, :].astype(np.float32, copy=True), self.bad_words)
            forced = int(np.argmax(logits))
        sequence = [forced]

        current = np.array([[forced]], dtype=np.int64)
        for _step in range(max_new):
            outputs = self._dec_past.run(
                None, build_decoder_feed(self._dec_past, current, attention_mask, None, past))
            logits = mask_tokens(outputs[0][:, -1, :].astype(np.float32, copy=True), self.bad_words)
            token = int(np.argmax(logits[0]))
            present = collect_present(self._dec_past, outputs)
            past.update(present)  # cross-attention KV only arrives from the first pass
            if token == self.eos_token_id:
                break
            sequence.append(token)
            current = np.array([[token]], dtype=np.int64)

        return sequence[1:]  # drop the imposed language token

    def describe(self) -> str:
        if self._enc is None:
            return "模型未加载"
        return "\n".join([
            f"engine         : NLLB ({self.src_lang} -> {self.tgt_lang})",
            f"start/eos/pad  : {self.start_token_id}/{self.eos_token_id}/{self.pad_token_id}",
            f"forced token   : {self.forced_first_token}",
            f"greedy         : beams fixed at {self.num_beams}",
        ])


class TranslationRoute:
    """One or more translators applied in sequence.

    A pair without a direct model is served by two hops (``ru -> en -> zh``). Errors
    compound across hops, so this reports itself as a pivot.
    """

    def __init__(self, legs: list, label: str = "", is_pivot: bool = False) -> None:
        self.legs = legs
        self.label = label
        self.is_pivot = is_pivot
        self.load_seconds = 0.0
        self.last_seconds = 0.0

    def load(self) -> None:
        started = time.perf_counter()
        for leg in self.legs:
            leg.load()
        self.load_seconds = time.perf_counter() - started

    def set_beams(self, requested: int | None) -> None:
        for leg in self.legs:
            leg.set_beams(requested)

    @property
    def num_beams(self) -> int:
        return self.legs[0].num_beams if self.legs else 1

    @property
    def ready(self) -> bool:
        return bool(self.legs) and all(leg.ready for leg in self.legs)

    def translate(self, text: str) -> str:
        started = time.perf_counter()
        current = text
        for leg in self.legs:
            current = leg.translate(current)
            if not current:
                break
        self.last_seconds = time.perf_counter() - started
        return current

    def describe(self) -> str:
        head = f"route: {self.label}" + ("  [经中转，质量低于直译]" if self.is_pivot else "")
        return "\n".join([head] + [leg.describe() for leg in self.legs])
