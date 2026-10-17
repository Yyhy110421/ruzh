"""The test that actually matters: does a Russian screen line end up as good Chinese?

Exact OCR equality is not the bar - OCR will never be perfect, and a case error in
Russian barely affects translation. What matters is whether the text that reaches the
translator is real Russian instead of lookalike Latin, because only the former
produces a usable Chinese sentence.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "ru_pipeline.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.engine import build  # noqa: E402
from app.ocr_engine import OcrEngine  # noqa: E402

CASES = [
    "Встреча перенесена на три часа дня завтра.",
    "Цена указана за одну тонну без доставки.",
    "Спецодежда и обувь для сотрудников склада.",
    "Подтвердите дату поставки до пятницы.",
]

FONTS = ("segoeui.ttf", "arial.ttf", "calibri.ttf", "DejaVuSans.ttf")


def render(text: str, size: int = 48) -> np.ndarray:
    image = Image.new("RGB", (1600, 150), (10, 10, 14))
    draw = ImageDraw.Draw(image)
    font = None
    for name in FONTS:
        try:
            font = ImageFont.truetype(name, size)
            break
        except Exception:  # noqa: BLE001
            continue
    draw.text((28, 44), text, font=font, fill=(245, 245, 250))
    return np.asarray(image)


def main() -> int:
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    root = str(ROOT / "models")

    ocr = OcrEngine(cfg.ocr, models_root=root, script="cyrillic")
    ocr.load()
    print(f"OCR 使用西里尔专用模型: {ocr.uses_script_model}  ({ocr.load_seconds:.1f}s)")
    print()

    route = build(cfg, root)
    route.load()
    print(f"翻译路线: {route.label}")
    print("=" * 78)

    for text in CASES:
        recognised = (ocr.recognize(render(text)).text or "").strip()
        translation = route.translate(recognised)
        print(f"原文  : {text}")
        print(f"识别  : {recognised}")
        print(f"译文  : {translation}")

        cyrillic = sum(1 for ch in recognised if "\u0400" <= ch <= "\u04ff")
        latin = sum(1 for ch in recognised if ch.isascii() and ch.isalpha())
        ratio = cyrillic / max(1, cyrillic + latin)
        flag = "OK  " if ratio > 0.9 else "WARN"
        print(f"      [{flag}] 西里尔字母占比 {ratio:.0%}"
              f"（低＝又被认成拉丁字母了）")
        print("-" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
