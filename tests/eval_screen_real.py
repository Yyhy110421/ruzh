# -*- coding: utf-8 -*-
"""The headline feature, inside the artifact that ships.

Everything proved so far is that the customer build starts: bootstrap, config, models
present, the window appears. None of it proved the thing the product is bought for - that
a picture of Russian text becomes Chinese. So: draw Russian text on an image, run it
through the shipped build's OCR, then through the shipped build's translator.

    runtime/python/python.exe smoke_screen.py <customer folder>
"""
import os
import sys
import time
from pathlib import Path

PKG = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(PKG))
os.chdir(PKG)

print("交付件:", PKG)
print()

from app import bootstrap
bootstrap.prepare()

from app import config as config_mod
cfg = config_mod.AppConfig()
cfg.source_lang, cfg.target_lang = "ru", "zh"
print(f"[1] 方向 {cfg.source_lang} -> {cfg.target_lang}")

LINE = "Срок поставки 30 календарных дней с момента оплаты."

from PIL import Image, ImageDraw, ImageFont
font = None
for candidate in ("arial.ttf", "segoeui.ttf", "tahoma.ttf"):
    try:
        font = ImageFont.truetype(candidate, 34)
        print(f"[2] 字体 {candidate}")
        break
    except Exception:
        continue
if font is None:
    font = ImageFont.load_default()
    print("[2] 用默认字体")

image = Image.new("RGB", (1400, 90), "white")
ImageDraw.Draw(image).text((16, 20), LINE, fill="black", font=font)
print(f"[3] 造了一张图：{image.size}")

import numpy as np
from app.ocr_engine import OcrEngine, script_for

script = script_for(cfg.source_lang)
engine = OcrEngine(cfg.ocr, models_root=cfg.resolved_models_root(), script=script)
t0 = time.perf_counter()
engine.load()
print(f"[4] OCR 就绪 {time.perf_counter()-t0:.1f} 秒（{script} 模型，"
      f"用上了吗={engine.uses_script_model}）")
t0 = time.perf_counter()
result = engine.recognize(np.array(image))
print(f"[5] 识别 {time.perf_counter()-t0:.1f} 秒")
print(f"    识别到: {result.text!r}")
for line in result.lines:
    print(f"      {line.score:.2f}  {line.text!r}")

from app.text_tool import translate_text
t0 = time.perf_counter()
out = translate_text(cfg, cfg.resolved_models_root(), result.text, mode="paragraph")
print(f"[6] 翻译 {time.perf_counter()-t0:.1f} 秒")
print(f"    译文: {out.translation!r}")

ok_text = bool(result.text and "30" in result.text)
ok_trans = bool(out.translation.strip()) and not out.failed
print()
print(f"识别成功: {ok_text}")
print(f"翻译成功: {ok_trans}")
print("结论：" + ("交付件的屏幕翻译主链路通了" if ok_text and ok_trans else "主链路有问题"))
sys.exit(0 if (ok_text and ok_trans) else 1)
