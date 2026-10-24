"""Configuration model and JSON persistence."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = APP_DIR / "config.json"
MODELS_DIR = APP_DIR / "models"
LOG_DIR = APP_DIR / "logs"


@dataclass
class Region:
    """Screen region in physical pixels."""

    left: int = 0
    top: int = 0
    width: int = 0
    height: int = 0

    @property
    def valid(self) -> bool:
        return self.width >= 8 and self.height >= 8

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.left, self.top, self.width, self.height)


@dataclass
class OcrSettings:
    #: Drop recognised lines whose confidence is below this value.
    min_score: float = 0.5
    #: Longest side the detector resizes to; smaller is faster, larger finds smaller text.
    max_side_len: int = 1280
    #: 0 lets onnxruntime pick. Lower leaves cores free for the game or video.
    intra_op_threads: int = 0


@dataclass
class TranslateSettings:
    max_length: int = 256
    #: Chinese output from Marian models is often space separated; join CJK runs.
    join_cjk: bool = True
    #: Turn "," into "，" when it sits next to Chinese text.
    normalize_punctuation: bool = True
    #: Text is only retranslated when the OCR result actually changed.
    cache_size: int = 512
    #: 1 = greedy, 0 = use the beam count the model recommends (usually 4).
    #:
    #: Measured on 15 subtitle-style sentences: 4 beams costs ~33% more latency and
    #: is not consistently better - it occasionally runs away and emits a trailing
    #: clause ("I've been waiting for this moment my whole life." became
    #: "我一直在等待这个时刻我的一生。"). Greedy is both faster and safer, so it is
    #: the default and beam search is opt-in.
    num_beams: int = 4
    length_penalty: float = 1.0
    #: Which engine serves translations. Empty means "choose automatically": the
    #: offline model when it is installed (no quota, nothing leaves the machine),
    #: otherwise the best keyless online engine. See app/engines/.
    # Default to the local model: it is the only offline engine whose output is usable
    # as-is. An empty string resolved to OPUS, which is the fastest and the worst -
    # a first run produced "Покупателя покупателя"-grade text and looked broken.
    engine: str = "local-llm"


@dataclass
class OverlaySettings:
    enabled: bool = True
    font_family: str = "Microsoft YaHei UI"
    font_size: int = 22
    text_color: str = "#FFFFFF"
    bg_color: str = "#101014"
    #: 0.0 fully transparent background, 1.0 fully opaque.
    bg_opacity: float = 0.88
    #: Also show the recognised source line above the translation.
    show_source: bool = False
    #: cover | below | above
    position: str = "cover"
    padding: int = 10
    max_lines: int = 6
    #: solid = translucent backdrop, transparent = text floats with no backdrop.
    background_mode: str = "solid"
    #: Let mouse clicks pass through to whatever is underneath.
    click_through: bool = True


@dataclass
class Hotkeys:
    start: str = "F9"
    stop: str = "F10"
    #: Pausing keeps the models loaded, so resuming is instant where stopping and
    #: starting again costs several seconds of model loading.
    pause: str = "F11"
    pick_region: str = "F8"


@dataclass
class AppConfig:
    region: Region = field(default_factory=Region)
    #: Chinese to Russian is the point of the product; the other pairs are there because
    #: the models happen to cover them, not because a customer of this one wants them.
    source_lang: str = "ru"
    target_lang: str = "zh"
    interval_ms: int = 800
    #: Consecutive identical OCR reads required before a line is translated.
    settle_frames: int = 1
    #: Fraction of characters two reads may differ by and still count as the same
    #: line. A similarity *ratio* is too blunt here: one wrong character in
    #: "Hello world." already drops the ratio to 0.92, below any sane threshold.
    jitter_tolerance: float = 0.08
    #: Subtitles often appear character by character. While the newly read text is
    #: merely a longer (or shorter) version of the current line, hold off instead of
    #: translating every intermediate state.
    defer_while_growing: bool = True
    #: Safety valve: translate anyway once a line has been growing for this many reads.
    max_defer_frames: int = 8
    #: Consecutive empty reads before the subtitle window is cleared.
    blank_clear_frames: int = 2
    ocr: OcrSettings = field(default_factory=OcrSettings)
    translate: TranslateSettings = field(default_factory=TranslateSettings)
    overlay: OverlaySettings = field(default_factory=OverlaySettings)
    hotkeys: Hotkeys = field(default_factory=Hotkeys)
    mirror: str = "https://hf-mirror.com"
    models_root: str = ""
    #: Credentials for the official Baidu translation API (appid + secret key).
    #: Only used when the selected engine is 联网 · 百度.
    baidu_appid: str = ""
    baidu_key: str = ""
    #: Bring-your-own-model: any OpenAI-compatible chat endpoint.
    # Pre-filled with the endpoint that gives the best Chinese output for the money.
    # It was empty, so choosing "联网·自定义大模型" did nothing at all - the engine just
    # reported "endpoint 未设置" and the user was stuck with the 1.5B local model.
    openai_base_url: str = "https://api.deepseek.com/v1"
    openai_api_key: str = ""
    openai_model: str = "deepseek-chat"
    #: Prompt template for that endpoint; {src} and {tgt} are filled with language
    #: names, {src_code}/{tgt_code} with the codes.
    openai_prompt: str = ""
    openai_timeout: float = 60.0
    #: Term glossary. Empty path means <project>/glossary.json.
    glossary_enabled: bool = True
    glossary_path: str = ""
    #: Hide company names, tax numbers, bank details, phone numbers and addresses before
    #: the text goes to an online engine, and put them back afterwards. Only ever applies
    #: to engines that send the text somewhere; the offline one has nothing to protect.
    redact_enabled: bool = True
    #: Use the bundled Chinese-Russian dictionary for short inputs, where a dictionary
    #: entry beats a translator guessing at a single word. See app/dictlookup.py.
    dictionary_enabled: bool = True
    #: Copies the recognised text to the clipboard as well.
    copy_to_clipboard: bool = False

    #: "region" = the selection is fixed screen coordinates, "window" = the selection
    #: is an offset inside a window's content area, so it follows the window when it is
    #: moved or resized. The latter is what people actually want: a fixed patch of
    #: screen stops pointing at the chat as soon as the chat window is dragged.
    target_mode: str = "region"
    #: Window to follow in "window" mode. The title is stored rather than the handle,
    #: because handles do not survive a restart; the process name disambiguates.
    target_window_title: str = ""
    target_window_process: str = ""

    def resolved_models_root(self) -> str:
        return self.models_root or str(MODELS_DIR)

    def resolved_glossary_path(self) -> str:
        return self.glossary_path or str(APP_DIR / "glossary.json")


#: Nested dataclasses keyed by the field name that holds them.
_NESTED: dict[str, type] = {
    "region": Region,
    "ocr": OcrSettings,
    "translate": TranslateSettings,
    "overlay": OverlaySettings,
    "hotkeys": Hotkeys,
}


def _from_dict(cls, data: dict):
    """Build *cls* from a plain dict, ignoring unknown keys and bad sections."""
    if not isinstance(data, dict):
        return cls()
    kwargs = {}
    for f in fields(cls):
        if f.name not in data:
            continue
        value = data[f.name]
        nested = _NESTED.get(f.name) if cls is AppConfig else None
        if nested is not None:
            if isinstance(value, dict):
                kwargs[f.name] = _from_dict(nested, value)
            continue
        kwargs[f.name] = value
    return cls(**kwargs)


def load(path: Path | None = None) -> AppConfig:
    path = Path(path or CONFIG_PATH)
    if not path.is_file():
        return AppConfig()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except Exception:  # noqa: BLE001
        return AppConfig()
    try:
        return _from_dict(AppConfig, data)
    except Exception:  # noqa: BLE001
        return AppConfig()


def save(cfg: AppConfig, path: Path | None = None) -> Path:
    path = Path(path or CONFIG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(asdict(cfg), fh, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def ensure_dirs() -> None:
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
