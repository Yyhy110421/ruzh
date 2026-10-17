"""Why does the Cyrillic model drop spaces, and does a wider input fix it?

PP-OCR resizes every detected text line to a fixed ``rec_image_shape`` before
recognition. The default is [3, 48, 320] - sized for roughly ten characters. A
subtitle line 1400px wide squeezed into 320px leaves a space under a pixel thick,
which the CTC decoder then drops. The models accept arbitrary width, so the test is
whether raising the width restores the word boundaries.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "ocr_widen.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

import numpy as np  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402
from rapidocr_onnxruntime import RapidOCR  # noqa: E402

MODEL_DIR = ROOT / "models" / "ocr" / "cyrillic_PP-OCRv3_rec"

print("=" * 78)
print("一、字典里有没有空格字符？")
print("=" * 78)
raw = (MODEL_DIR / "dict.txt").read_text(encoding="utf-8")
lines = raw.splitlines()
print(f"字典共 {len(lines)} 行")
space_like = [(i, repr(ch)) for i, ch in enumerate(lines)
              if ch.strip() == "" and ch != ""]
print(f"  其中空白但非空的行（可能就是空格）：{space_like[:5]}")
print(f"  第一行是空行吗：{lines[0]!r}")
print(f"  含真正的空格字符：{' ' in raw}")

print()
print("=" * 78)
print("二、确认 RapidOCR 到底把长句压扁还是截断")
print("=" * 78)
import inspect  # noqa: E402
from rapidocr_onnxruntime.ch_ppocr_rec import TextRecognizer  # noqa: E402

try:
    src = inspect.getsource(TextRecognizer.resize_norm_img)
    for line in src.splitlines():
        if any(k in line for k in ("rec_image_shape", "ratio", "resize", "max_wh_ratio")):
            print("   ", line.strip()[:110])
except Exception as exc:  # noqa: BLE001
    print(f"  读取失败：{exc}")

print()
print("=" * 78)
print("三、同一句俄语，不同识别宽度下的结果")
print("=" * 78)

SAMPLES = [
    "Спецодежда и обувь для сотрудников склада.",
    "Цена указана за одну тонну без доставки.",
    "Подтвердите дату поставки до пятницы.",
]


def render(text: str, width: int = 1500, font_size: int = 46) -> np.ndarray:
    image = Image.new("RGB", (width, 130), (10, 10, 14))
    draw = ImageDraw.Draw(image)
    font = None
    for name in ("segoeui.ttf", "arial.ttf", "calibri.ttf"):
        try:
            font = ImageFont.truetype(name, font_size)
            break
        except Exception:  # noqa: BLE001
            continue
    draw.text((24, 36), text, font=font, fill=(245, 245, 250))
    return np.asarray(image)


def engine_for(width: int) -> RapidOCR:
    return RapidOCR(
        text_score=0.5, use_cls=False, width_height_ratio=-1,
        max_side_len=1600, min_side_len=12,
        det_limit_type="max", det_limit_side_len=1600,
        rec_model_path=str(MODEL_DIR / "model.onnx"),
        rec_keys_path=str(MODEL_DIR / "dict.txt"),
        rec_img_shape=[3, 48, width],
    )


results: dict[int, list[str]] = {}
for width in (320, 640, 960, 1600):
    try:
        engine = engine_for(width)
    except Exception as exc:  # noqa: BLE001
        print(f"\n[宽度 {width}] 引擎初始化失败：{exc}")
        continue
    print(f"\n[识别宽度 {width}]")
    texts = []
    for text in SAMPLES:
        try:
            out, _ = engine(render(text))
            got = (out[0][1] if out else "").strip()
        except Exception as exc:  # noqa: BLE001
            got = f"<{type(exc).__name__}: {exc}>"
        texts.append(got)
        spaces_ok = " " in got
        print(f"   原文 {text}")
        print(f"   识别 {got}")
        print(f"        空格保住了？{'是' if spaces_ok else '否'}   完全一致？"
              f"{'是' if got == text else '否'}")
    results[width] = texts

print()
print("=" * 78)
print("汇总：空格保留情况")
print("=" * 78)
for width, texts in results.items():
    kept = sum(1 for t in texts if " " in t)
    exact = sum(1 for i, t in enumerate(texts) if t == SAMPLES[i])
    print(f"  宽度 {width:5d}  空格保住的句子 {kept}/{len(texts)}  完全正确 {exact}/{len(texts)}")
