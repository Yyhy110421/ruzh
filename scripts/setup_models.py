"""One command to get this program ready to translate.

    python scripts/setup_models.py

What it does, and why it is one command rather than a list:

* the offline translation models are 688 MB and cannot be committed to a repository, so
  they are the one download a new clone needs. Doing it by hand is the point where most
  people give up, so it happens here, with progress and resume.
* the Cyrillic recognition model ships inside the package (app/data/ocr). Without it a
  Russian document cannot be read at all, and 8.6 MB is not worth a cliff in front of the
  first thing anyone tries.
* it ends by checking that the pieces actually load, because "download finished" and
  "this will run" are not the same sentence.

Run it with no arguments for the languages this program is for (Chinese and Russian, both
directions). Pass pairs to get others:

    python scripts/setup_models.py en-zh zh-en ja-en
"""

from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import models, ocr_engine  # noqa: E402

#: Chinese and Russian, both directions. Russian goes through English because
#: Helsinki-NLP never published ru-zh, so zh-ru needs en-ru and en-zh as well - which is
#: why "just the two directions" still means four model directories.
DEFAULT_PAIRS = ("ru-en", "en-zh", "en-ru", "zh-en")


def parse_pair(text: str) -> tuple[str, str]:
    for sep in ("->", "-", "_"):
        if sep in text:
            src, _, tgt = text.partition(sep)
            return src.strip(), tgt.strip()
    raise argparse.ArgumentTypeError(f"无法解析语言对: {text}")


def main() -> int:
    parser = argparse.ArgumentParser(description="准备运行环境（下载离线模型）")
    parser.add_argument("pairs", nargs="*", type=parse_pair,
                        help=f"语言对，默认 {' '.join(DEFAULT_PAIRS)}")
    parser.add_argument("--root", default=os.path.join(ROOT, "models"), help="模型存放目录")
    parser.add_argument("--mirror", default=models.DEFAULT_MIRROR, help="模型镜像地址")
    parser.add_argument("--connections", type=int, default=6, help="每个文件的并发连接数")
    parser.add_argument("--check", action="store_true", help="只检查，不下载")
    args = parser.parse_args()

    pairs = args.pairs or [parse_pair(p) for p in DEFAULT_PAIRS]

    from scripts import download_models

    print("=" * 66)
    print("守译 · 环境准备")
    print("=" * 66)
    print()

    print("[1/2] 俄文识别模型")
    if ocr_engine.script_available(args.root, "cyrillic"):
        where = ocr_engine.script_model_dir(args.root, "cyrillic")
        print(f"      已就绪：{where}")
    else:
        # It ships in the package, so this only happens when somebody deleted it.
        print("      [缺失] app/data/ocr/cyrillic_PP-OCRv3_rec/model.onnx")
        print("      重新下载一份完整的发行包，或者手动放回这个目录。")
        return 1
    print()

    print("[2/2] 离线翻译模型（约 690 MB，只下一次）")
    if args.check:
        missing = [f"{s}-{t}" for s, t in pairs if not models.is_installed(args.root, s, t)]
        if missing:
            print(f"      缺：{'  '.join(missing)}")
            print("      去掉 --check 再跑一次就会下载。")
            return 1
        print("      全部已就绪")
        print()
        return 0

    def on_file(rel: str, frac: float) -> None:
        if frac == 0.0:
            print(f"      -> {rel}")

    import time

    state = {"last": 0.0}

    def on_progress(done: int, total: int) -> None:
        now = time.time()
        if now - state["last"] < 1.0 and done < total:
            return
        state["last"] = now
        pct = (done / total * 100) if total else 0.0
        print(f"\r      {done / 1048576:7.1f}/{total / 1048576:7.1f} MB  {pct:5.1f}%",
              end="", flush=True)

    failures = download_models.download_pairs(
        args.root, pairs, mirror=args.mirror, connections=args.connections,
        on_file=on_file, on_progress=on_progress)

    print()
    if failures:
        print(f"有 {failures} 组没下成。网络断了就再跑一次，会接着上次的继续。")
        return 1

    print("[好了] 现在运行：  python main.py")
    print("       第一次启动会加载模型，等几秒是正常的。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
