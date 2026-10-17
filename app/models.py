"""Offline model registry.

Two kinds of engine are supported:

* **OPUS-MT (Marian)** - one small ONNX model per language pair. Models exist for
  `en`/`zh`/`ru`, but a direct `ru<->zh` model was never published, so those pairs
  are routed through English (:data:`PIVOT_ROUTES`).
* **NLLB-200** - a single multilingual ONNX model covering every pair directly,
  including `ru<->zh`. Much larger and slower on CPU, so it is opt-in.

Models are pulled through a mirror so the app works where huggingface.co is blocked.
"""

from __future__ import annotations

import os

DEFAULT_MIRROR = os.environ.get("SCREEN_TRANSLATOR_HF_MIRROR", "https://hf-mirror.com")

# Quantized variants keep a pair around 110-160 MB while staying fast on CPU.
_ONNX_QUANTIZED = [
    "onnx/encoder_model_quantized.onnx",
    "onnx/decoder_model_quantized.onnx",
    "onnx/decoder_with_past_model_quantized.onnx",
]

_ONNX_FP32 = [
    "onnx/encoder_model.onnx",
    "onnx/decoder_model.onnx",
    "onnx/decoder_with_past_model.onnx",
]

_TOKENIZER_FILES = [
    "config.json",
    "generation_config.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "tokenizer.json",
    "source.spm",
    "target.spm",
    "vocab.json",
]

#: Human readable names for the language codes we ship.
LANGUAGE_NAMES = {
    "zh": "中文",
    "en": "英语",
    "ru": "俄语",
    "ja": "日语",
    "ko": "韩语",
    "fr": "法语",
    "de": "德语",
    "es": "西班牙语",
}

#: Language pairs with a direct OPUS-MT model. These are what the app pre-downloads.
MODEL_REGISTRY: dict[tuple[str, str], dict] = {
    ("en", "zh"): {
        "repo": "Xenova/opus-mt-en-zh",
        "label": "英语 → 中文 (opus-mt-en-zh)",
        "files": _TOKENIZER_FILES + _ONNX_QUANTIZED,
    },
    ("zh", "en"): {
        "repo": "Xenova/opus-mt-zh-en",
        "label": "中文 → 英语 (opus-mt-zh-en)",
        "files": _TOKENIZER_FILES + _ONNX_QUANTIZED,
    },
    ("en", "ru"): {
        "repo": "Xenova/opus-mt-en-ru",
        "label": "英语 → 俄语 (opus-mt-en-ru)",
        "files": _TOKENIZER_FILES + _ONNX_QUANTIZED,
    },
    ("ru", "en"): {
        "repo": "Xenova/opus-mt-ru-en",
        "label": "俄语 → 英语 (opus-mt-ru-en)",
        "files": _TOKENIZER_FILES + _ONNX_QUANTIZED,
    },
}

#: Pairs without a direct model, expressed as two hops through a pivot language.
#:
#: Helsinki-NLP never published ru<->zh, so both directions go through English.
#: Quality is worse than a direct model (errors compound over two passes), which is
#: exactly why the NLLB engine exists as an alternative.
PIVOT_ROUTES: dict[tuple[str, str], tuple[str, str, str]] = {
    ("ru", "zh"): ("ru", "en", "zh"),
    ("zh", "ru"): ("zh", "en", "ru"),
}

#: Extra single-hop pairs that exist upstream and can be fetched on demand.
OPTIONAL_PAIRS: dict[tuple[str, str], str] = {
    ("ja", "en"): "Xenova/opus-mt-ja-en",
    ("en", "ja"): "Xenova/opus-mt-en-jap",
    ("ko", "en"): "Xenova/opus-mt-ko-en",
    ("en", "ko"): "Xenova/opus-mt-en-ko",
    ("fr", "en"): "Xenova/opus-mt-fr-en",
    ("en", "fr"): "Xenova/opus-mt-en-fr",
    ("de", "en"): "Xenova/opus-mt-de-en",
    ("en", "de"): "Xenova/opus-mt-en-de",
}

#: Multilingual engines: one model, every pair, no pivot needed.
MULTILINGUAL: dict[str, dict] = {
    "nllb-200-distilled-600M": {
        "repo": "Xenova/nllb-200-distilled-600M",
        "label": "NLLB-200 distilled 600M",
        #: Rough on-disk footprint, shown in the UI before a long download.
        "approx_mb": 1270,
        "languages": {
            "zh": "zho_Hans",
            "en": "eng_Latn",
            "ru": "rus_Cyrl",
            "ja": "jpn_Jpan",
            "ko": "kor_Hang",
            "fr": "fra_Latn",
            "de": "deu_Latn",
            "es": "spa_Latn",
        },
        "files": [
            "config.json",
            "generation_config.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "tokenizer.json",
            "sentencepiece.bpe.model",
        ] + _ONNX_QUANTIZED,
    },
}

# The catalog of translation engines now lives in app/engines/; this module
# only knows about model files on disk.


def pair_key(src: str, tgt: str) -> tuple[str, str]:
    return (src.strip().lower(), tgt.strip().lower())


def get_spec(src: str, tgt: str) -> dict | None:
    """Direct single-hop model for a pair, if one is registered."""
    key = pair_key(src, tgt)
    if key in MODEL_REGISTRY:
        return MODEL_REGISTRY[key]
    if key in OPTIONAL_PAIRS:
        return {
            "repo": OPTIONAL_PAIRS[key],
            "label": f"{LANGUAGE_NAMES.get(key[0], key[0])} → "
                     f"{LANGUAGE_NAMES.get(key[1], key[1])} ({OPTIONAL_PAIRS[key]})",
            "files": _TOKENIZER_FILES + _ONNX_QUANTIZED,
        }
    return None


def resolve_route(src: str, tgt: str) -> list[tuple[str, str]]:
    """OPUS legs needed for a pair: one hop, or two through the pivot language."""
    key = pair_key(src, tgt)
    if get_spec(*key) is not None:
        return [key]
    pivot = PIVOT_ROUTES.get(key)
    if pivot is None:
        return []
    return [(pivot[0], pivot[1]), (pivot[1], pivot[2])]


def route_label(src: str, tgt: str) -> str:
    legs = resolve_route(src, tgt)
    if not legs:
        return ""
    if len(legs) == 1:
        spec = get_spec(*legs[0])
        return spec["label"] if spec else ""
    names = [LANGUAGE_NAMES.get(legs[0][0], legs[0][0])]
    names += [LANGUAGE_NAMES.get(leg[1], leg[1]) for leg in legs]
    return " → ".join(names) + "（经英语中转）"


def available_pairs() -> list[tuple[str, str]]:
    return sorted(MODEL_REGISTRY)


def all_supported_pairs() -> list[tuple[str, str]]:
    """Every pair the OPUS engine can serve, direct or pivoted."""
    pairs = set(MODEL_REGISTRY)
    for src, pivot, tgt in PIVOT_ROUTES.values():
        pairs.add((src, tgt))
    return sorted(pairs)


# -- OPUS pair storage ------------------------------------------------------
def cache_dir(models_root: str, src: str, tgt: str) -> str:
    return os.path.join(models_root, f"{src}-{tgt}")


def pair_installed(models_root: str, src: str, tgt: str) -> bool:
    spec = get_spec(src, tgt)
    if spec is None:
        return False
    root = cache_dir(models_root, src, tgt)
    for rel in spec["files"]:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return False
    return True


def is_installed(models_root: str, src: str, tgt: str) -> bool:
    """True when every leg the OPUS route needs is on disk."""
    legs = resolve_route(src, tgt)
    return bool(legs) and all(pair_installed(models_root, *leg) for leg in legs)


def missing_files(models_root: str, src: str, tgt: str) -> list[str]:
    spec = get_spec(src, tgt)
    if spec is None:
        return []
    root = cache_dir(models_root, src, tgt)
    out = []
    for rel in spec["files"]:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            out.append(rel)
    return out


def route_missing(models_root: str, src: str, tgt: str) -> list[str]:
    """Missing-file summary across every leg of the route."""
    out: list[str] = []
    for leg in resolve_route(src, tgt):
        missing = missing_files(models_root, *leg)
        if missing:
            spec = get_spec(*leg)
            out.append(f"{spec['label'] if spec else leg}: 缺 {len(missing)} 个文件")
    return out


# -- multilingual engines ---------------------------------------------------
def multilingual_spec(key: str) -> dict | None:
    return MULTILINGUAL.get(key)


def multilingual_dir(models_root: str, key: str) -> str:
    return os.path.join(models_root, key)


def multilingual_installed(models_root: str, key: str) -> bool:
    spec = MULTILINGUAL.get(key)
    if spec is None:
        return False
    root = multilingual_dir(models_root, key)
    for rel in spec["files"]:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            return False
    return True


def multilingual_missing(models_root: str, key: str) -> list[str]:
    spec = MULTILINGUAL.get(key)
    if spec is None:
        return []
    root = multilingual_dir(models_root, key)
    out = []
    for rel in spec["files"]:
        path = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            out.append(rel)
    return out


def nllb_language(code: str) -> str | None:
    """Map a short code like ``ru`` to the NLLB token ``rus_Cyrl``."""
    spec = MULTILINGUAL["nllb-200-distilled-600M"]
    return spec["languages"].get(code.strip().lower())

