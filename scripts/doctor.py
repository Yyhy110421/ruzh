"""Environment self-check for the screen translator.

    python scripts/doctor.py            # checks imports, OCR and translation
    python scripts/doctor.py --capture  # also grabs the saved region and OCRs it

Writes nothing except the report; safe to run any time.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

try:  # keep the report readable regardless of the console codepage
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

PASS, FAIL, WARN, INFO = "PASS", "FAIL", "WARN", "INFO"

#: The multilingual model's key in app/models.py.
NLLB_KEY = "nllb-200-distilled-600M"

_results: list[tuple[str, str, str]] = []


def record(status: str, name: str, detail: str = "") -> None:
    _results.append((status, name, detail))
    marker = {PASS: "[ OK ]", FAIL: "[FAIL]", WARN: "[WARN]", INFO: "[ -- ]"}[status]
    print(f"{marker} {name}" + (f"  -- {detail}" if detail else ""), flush=True)


def check_python() -> None:
    version = ".".join(str(p) for p in sys.version_info[:3])
    if sys.version_info >= (3, 10):
        record(PASS, "Python 版本", f"{version} ({sys.executable})")
    else:
        record(FAIL, "Python 版本", f"{version} 需要 3.10 及以上")


def check_imports() -> None:
    modules = [
        ("numpy", "numpy"),
        ("mss", "mss"),
        ("PIL", "pillow"),
        ("cv2", "opencv-python"),
        ("onnxruntime", "onnxruntime"),
        ("rapidocr_onnxruntime", "rapidocr-onnxruntime"),
        ("transformers", "transformers"),
    ]
    for name, label in modules:
        try:
            started = time.perf_counter()
            __import__(name)
            record(PASS, f"导入 {label}", f"{(time.perf_counter() - started) * 1000:.0f} ms")
        except Exception as exc:  # noqa: BLE001
            hint = ""
            if name == "onnxruntime":
                hint = "（多为缺少 VC++ 运行库，请运行 install.bat 或 scripts/setup_env.py）"
            record(FAIL, f"导入 {label}", f"{exc}{hint}")


def check_monitors() -> None:
    try:
        from app.capture import ScreenCapture

        capture = ScreenCapture()
        virtual = capture.virtual_screen()
        monitors = capture.monitors()
        record(PASS, "显示器", f"虚拟桌面 {virtual['width']}x{virtual['height']} @ "
                               f"({virtual['left']}, {virtual['top']})，{len(monitors)} 个显示器")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "显示器", str(exc))


def check_ocr() -> None:
    try:
        from PIL import Image, ImageDraw, ImageFont

        from app.config import OcrSettings
        from app.ocr_engine import OcrEngine

        font = None
        for path in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                     r"C:\Windows\Fonts\arial.ttf"):
            if os.path.exists(path):
                font = ImageFont.truetype(path, 36)
                break
        img = Image.new("RGB", (760, 150), (16, 16, 20))
        draw = ImageDraw.Draw(img)
        draw.text((24, 20), "Offline OCR smoke test 123", font=font, fill=(240, 240, 240))
        draw.text((24, 80), "离线识别自检中文", font=font, fill=(240, 240, 240))

        import numpy as np

        engine = OcrEngine(OcrSettings())
        t0 = time.perf_counter()
        engine.load()
        load_s = time.perf_counter() - t0
        result = engine.recognize(np.ascontiguousarray(np.array(img)[:, :, ::-1]))
        record(PASS, "离线 OCR", f"加载 {load_s:.1f}s，识别 {result.elapsed * 1000:.0f} ms")
        if result.text:
            record(PASS, "OCR 识别结果", result.text)
        else:
            record(WARN, "OCR 识别结果", "没有识别出文字（模型可用但结果为空）")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "离线 OCR", str(exc))
        traceback.print_exc()


def check_models() -> None:
    try:
        from app import models

        root = os.path.join(ROOT, "models")
        for src, tgt in models.all_supported_pairs():
            label = models.route_label(src, tgt)
            if models.is_installed(root, src, tgt):
                record(PASS, f"OPUS {src}->{tgt}", label)
            else:
                missing = models.route_missing(root, src, tgt)
                record(WARN, f"OPUS {src}->{tgt}",
                       ("；".join(missing) or "未下载") + "，可在程序里点「下载模型」")

        for key, spec in models.MULTILINGUAL.items():
            if models.multilingual_installed(root, key):
                record(PASS, f"多语言 {key}", spec["label"])
            else:
                # Deliberately not installed: measured at +2 GB RAM and 11-45 s per
                # sentence on this 8 GB machine, so it is not a problem to report.
                record(INFO, f"多语言 {key}（可选，未安装）",
                       f"约 {spec['approx_mb']} MB。实测内存 +2 GB、每句 11–45 秒，"
                       f"不建议在这台机器上使用；确实需要时用 --nllb 下载")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "模型检查", str(exc))


def check_translation() -> None:
    try:
        from app import config as config_mod
        from app import engine as engine_mod
        from app import models

        root = os.path.join(ROOT, "models")
        sample = "Please remember to save your work before closing the application."

        for src, tgt, label in (("en", "zh", "英→中"), ("ru", "zh", "俄→中（经中转）")):
            cfg = config_mod.AppConfig()
            cfg.source_lang, cfg.target_lang = src, tgt
            cfg.translate.engine = "opus"
            ready, detail = engine_mod.check(cfg, root)
            if not ready:
                record(WARN, f"离线翻译 {label}", detail)
                continue
            route = engine_mod.build(cfg, root)
            t0 = time.perf_counter()
            route.load()
            record(PASS, f"翻译模型加载 {label}",
                   f"{time.perf_counter() - t0:.1f}s，{route.label}")
            t0 = time.perf_counter()
            out = route.translate("The meeting has been moved to three o'clock tomorrow afternoon."
                                  if src != "ru" else
                                  "Встреча перенесена на три часа дня завтра.")
            record(PASS, f"离线翻译 {label}",
                   f"{(time.perf_counter() - t0) * 1000:.0f} ms -> {out}")

        key = NLLB_KEY
        if models.multilingual_installed(root, key):
            # Run in a separate process: see check_nllb_subprocess().
            check_nllb_subprocess()
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "离线翻译", str(exc))
        traceback.print_exc()


def check_capture() -> None:
    try:
        from app import config as config_mod
        from app.capture import ScreenCapture
        from app.ocr_engine import OcrEngine

        cfg = config_mod.load()
        if not cfg.region.valid:
            record(WARN, "区域截屏", "配置里还没有保存区域，先在程序里框选一次")
            return
        frame = ScreenCapture().grab(cfg.region)
        record(PASS, "区域截屏", f"{frame.shape[1]}x{frame.shape[0]} 像素")
        result = OcrEngine(cfg.ocr).recognize(frame)
        record(PASS if result.text else WARN, "当前区域识别",
               result.text[:120] or "当前区域没有文字")
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "区域截屏", str(exc))


def check_online() -> None:
    """Probe each online engine that claims to support en->zh.

    Spends a few characters of the free quota, which is the price of knowing whether
    the network path actually works before relying on it.
    """
    try:
        from app import config as config_mod
        from app import engine as engine_mod
        from app import models

        probe = "Please save the file before closing."
        online_ids = [k for k, spec in engine_mod.catalog().items()
                      if spec.kind in ("online", "http")]
        for key in online_ids:
            label = engine_mod.engine_label(key)
            cfg = config_mod.AppConfig()
            cfg.translate.engine = key
            cfg.source_lang, cfg.target_lang = "en", "zh"
            ready, detail = engine_mod.check(cfg, os.path.join(ROOT, "models"))
            if not ready:
                record(INFO, f"联网 {label}", detail)
                continue
            try:
                route = engine_mod.build(cfg, os.path.join(ROOT, "models"))
                route.load()
                t0 = time.perf_counter()
                out = route.translate(probe)
                ms = (time.perf_counter() - t0) * 1000
                record(PASS, f"联网 {label}", f"{ms:.0f} ms -> {out}")
            except Exception as exc:  # noqa: BLE001
                record(WARN, f"联网 {label}", str(exc))
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "在线引擎检查", str(exc))


def check_nllb_subprocess() -> None:
    """Test NLLB in its own process.

    Loading it after the OPUS sessions works only if the allocator can still hand out
    a single ~1 GB block for the decoder's dequantized embedding matrix, which it
    cannot always do. Running it fresh also matches how the app uses it: one engine
    loaded at a time.
    """
    from app import models

    root = os.path.join(ROOT, "models")
    key = NLLB_KEY
    if not models.multilingual_installed(root, key):
        record(INFO, "NLLB", "模型未安装（可选），跳过")
        return

    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--nllb-only"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=600,
        )
    except Exception as exc:  # noqa: BLE001
        record(FAIL, "NLLB", f"子进程执行失败：{exc}")
        return

    line = ""
    for candidate in reversed((proc.stdout or "").splitlines()):
        if candidate.startswith("NLLB-RESULT "):
            line = candidate[len("NLLB-RESULT "):].strip()
            break
    if not line:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()
        record(FAIL, "NLLB", tail[-1] if tail else "子进程没有返回结果")
        return
    status, _, detail = line.partition(" | ")
    record(PASS if status == "OK" else FAIL, "NLLB 俄→中直译", detail)


def nllb_only() -> int:
    """Entry point used by --nllb-only: prints one machine readable result line."""
    from app import bootstrap

    bootstrap.prepare()
    from app import config as config_mod
    from app import engine as engine_mod
    from app import models

    root = os.path.join(ROOT, "models")
    try:
        cfg = config_mod.AppConfig()
        cfg.source_lang, cfg.target_lang = "ru", "zh"
        cfg.translate.engine = "nllb"
        route = engine_mod.build(cfg, root)
        t0 = time.perf_counter()
        route.load()
        load_s = time.perf_counter() - t0
        t0 = time.perf_counter()
        out = route.translate("Встреча перенесена на три часа дня завтра.")
        ms = (time.perf_counter() - t0) * 1000
        print(f"NLLB-RESULT OK | 加载 {load_s:.1f}s，每句约 {ms:.0f} ms -> {out}")
    except Exception as exc:  # noqa: BLE001
        print(f"NLLB-RESULT FAIL | {exc}")
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="守译 环境自检")
    parser.add_argument("--capture", action="store_true", help="顺便截取已保存的区域并识别")
    parser.add_argument("--nllb-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.nllb_only:
        return nllb_only()

    print("=" * 62)
    print("守译 - 环境自检")
    print("=" * 62)

    from app import bootstrap

    bootstrap.prepare()

    check_python()
    check_imports()
    check_monitors()
    check_ocr()
    check_models()
    check_translation()
    check_online()
    if args.capture:
        check_capture()

    failures = [item for item in _results if item[0] == FAIL]
    warnings = [item for item in _results if item[0] == WARN]
    passes = [item for item in _results if item[0] == PASS]
    infos = [item for item in _results if item[0] == INFO]
    print("=" * 62)
    print(f"通过 {len(passes)} 项，警告 {len(warnings)} 项，失败 {len(failures)} 项"
          + (f"，跳过 {len(infos)} 项" if infos else ""))
    if warnings:
        print("\n注意：")
        for _status, name, detail in warnings:
            print(f"  - {name}: {detail}")
    if failures:
        print("\n需要处理：")
        for _status, name, detail in failures:
            print(f"  - {name}: {detail}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
