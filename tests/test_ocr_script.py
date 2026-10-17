"""Does the Cyrillic recognition model actually fix Russian OCR?

The bundled Chinese+Latin model returns lookalike Latin for Cyrillic (Встреча ->
"BcTpe4a"), which translates to fluent nonsense. This checks each language against
the model the app now selects for it.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "ocr_script.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.config import AppConfig  # noqa: E402
from app.ocr_engine import OcrEngine, script_for  # noqa: E402

SAMPLES = [
    ("en", "latin", "The meeting has been moved to three tomorrow afternoon."),
    ("ru", "cyrillic", "Встреча перенесена на три часа дня завтра."),
    ("ru", "cyrillic", "Спецодежда и обувь для сотрудников склада."),
    ("en", "latin", "Please confirm the delivery date by Friday."),
    ("ru", "cyrillic", "Цена указана за одну тонну без доставки."),
]

FONT_CANDIDATES = ("segoeui.ttf", "arial.ttf", "calibri.ttf", "DejaVuSans.ttf")


def render(text: str, font_size: int = 46) -> np.ndarray:
    image = Image.new("RGB", (1400, 150), (10, 10, 14))
    draw = ImageDraw.Draw(image)
    font = None
    for name in FONT_CANDIDATES:
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except Exception:  # noqa: BLE001
            continue
    draw.text((30, 45), text, font=font, fill=(245, 245, 250))
    return np.asarray(image)


failures = 0
by_script: dict[str, OcrEngine] = {}

for lang, script, text in SAMPLES:
    if script not in by_script:
        engine = OcrEngine(AppConfig().ocr, models_root=str(ROOT / "models"),
                           script=script)
        engine.load()
        by_script[script] = engine
        print(f"引擎 [{script}] 使用专用模型: {engine.uses_script_model}  "
              f"加载 {engine.load_seconds:.1f}s")
    engine = by_script[script]
    got = (engine.recognize(render(text)).text or "").strip()
    ok = got == text
    if not ok:
        failures += 1
    print(f"\n[{'OK  ' if ok else 'FAIL'}] {lang}/{script}")
    print(f"       原文: {text}")
    print(f"       识别: {got}")
    if not ok:
        # Show where it diverges, which is how the Latin-lookalike failure is spotted.
        wrong = [(a, b) for a, b in zip(text, got) if a != b]
        print(f"       不同处: {wrong[:12]}")

print()
print(f"失败 {failures} / {len(SAMPLES)}")
