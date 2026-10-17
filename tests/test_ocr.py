"""Dev smoke test: render a synthetic screenshot and OCR it with RapidOCR."""
import os
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import sys

FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\arial.ttf",
]


def pick_font(size):
    for path in FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def make_image():
    img = Image.new("RGB", (900, 320), (18, 18, 22))
    d = ImageDraw.Draw(img)
    d.text((30, 30), "The quick brown fox jumps over the lazy dog.", font=pick_font(34), fill=(240, 240, 240))
    d.text((30, 95), "Subtitle line two renders here.", font=pick_font(34), fill=(240, 240, 240))
    d.text((30, 160), "这是一行中文字幕，用于测试识别。", font=pick_font(34), fill=(240, 240, 240))
    d.text((30, 225), "混合 mixed 文本 12345 test", font=pick_font(34), fill=(240, 240, 240))
    return img


def main():
    from rapidocr_onnxruntime import RapidOCR

    img = make_image()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sample.png")
    img.save(out)
    print(f"[ok] sample image -> {out}")

    t0 = time.time()
    engine = RapidOCR()
    print(f"[ok] engine loaded in {time.time() - t0:.2f}s")

    arr = np.array(img)[:, :, ::-1]  # RGB -> BGR
    t0 = time.time()
    result, elapse = engine(arr)
    dt = time.time() - t0
    print(f"[ok] inference {dt * 1000:.0f} ms (elapse={elapse})")

    if not result:
        print("[FAIL] no text detected")
        return 1
    for box, text, score in result:
        print(f"  {score:.3f}  {text!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
