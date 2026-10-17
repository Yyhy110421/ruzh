"""Download offline translation models.

Usage:
    python scripts/download_models.py                     # default pairs
    python scripts/download_models.py en-zh zh-en ja-en
    python scripts/download_models.py --list
"""

from __future__ import annotations

import argparse
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Match the UTF-8 console that bootstrap.ps1 / install.bat set up.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

from app import downloader, models  # noqa: E402


def parse_pair(text: str) -> tuple[str, str]:
    for sep in ("->", "-", "_"):
        if sep in text:
            src, _, tgt = text.partition(sep)
            return src.strip(), tgt.strip()
    raise argparse.ArgumentTypeError(f"无法解析语言对: {text}")


def download_pairs(root: str, pairs, *, mirror: str = models.DEFAULT_MIRROR,
                   connections: int = 6, on_file=None, on_progress=None) -> int:
    """Download every pair, skipping the ones already there. Returns the failure count.

    Shared with scripts/setup_models.py, which is the entry point a new user runs: the
    dialogue (which pairs, what to print) belongs to the caller, not here.
    """
    failures = 0
    for src, tgt in pairs:
        label = models.route_label(src, tgt)
        if not label:
            print(f"[跳过] 没有 {src}->{tgt} 的模型定义")
            failures += 1
            continue
        if models.is_installed(root, src, tgt):
            print(f"[已完成] {label}")
            continue
        print(f"[下载] {label}")
        try:
            downloader.download_route(
                root, src, tgt, on_file=on_file, on_progress=on_progress,
                mirror=mirror, connections=connections)
            print(f"\r   [完成] {label}                    ")
        except Exception as exc:  # noqa: BLE001
            print(f"\r   [失败] {label}: {exc}")
            failures += 1
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="下载离线翻译模型")
    parser.add_argument("pairs", nargs="*", type=parse_pair,
                        help="语言对，例如 en-zh ru-zh（缺直连模型的会自动按中转路线下）")
    parser.add_argument("--root", default=os.path.join(ROOT, "models"), help="模型存放目录")
    parser.add_argument("--mirror", default=models.DEFAULT_MIRROR, help="模型镜像地址")
    parser.add_argument("--connections", type=int, default=6, help="每个文件的并发连接数")
    parser.add_argument("--nllb", action="store_true",
                        help="同时下载 NLLB-200 多语言模型（约 1.3 GB）")
    parser.add_argument("--list", action="store_true", help="列出内置语言对")
    args = parser.parse_args()

    if args.list or not args.pairs:
        if args.list:
            print("内置语言对（含中转路线）:")
            for src, tgt in models.all_supported_pairs():
                label = models.route_label(src, tgt)
                installed = "已下载" if models.is_installed(args.root, src, tgt) else "未下载"
                print(f"  {src}-{tgt}  {label}  [{installed}]")
            print("\n多语言模型:")
            for key, spec in models.MULTILINGUAL.items():
                installed = "已下载" if models.multilingual_installed(args.root, key) else "未下载"
                print(f"  {key}  {spec['label']}  约 {spec['approx_mb']} MB  [{installed}]")
            if not args.pairs:
                return 0
        args.pairs = models.available_pairs()

    def on_file(rel: str, frac: float) -> None:
        if frac == 0.0:
            print(f"   -> {rel}")

    state = {"last": 0.0}

    def on_progress(done: int, total: int) -> None:
        now = time.time()
        if now - state["last"] < 1.0 and done < total:
            return
        state["last"] = now
        pct = (done / total * 100) if total else 0.0
        print(f"\r       {done / 1048576:7.1f}/{total / 1048576:7.1f} MB  {pct:5.1f}%",
              end="", flush=True)

    failures = 0

    if args.nllb:
        for key, spec in models.MULTILINGUAL.items():
            if models.multilingual_installed(args.root, key):
                print(f"[已完成] {spec['label']}")
                continue
            print(f"[下载] {spec['label']}  仓库 {spec['repo']}  约 {spec['approx_mb']} MB")
            try:
                downloader.download_multilingual(
                    args.root, key, on_file=on_file, on_progress=on_progress,
                    mirror=args.mirror, connections=args.connections)
                print(f"\r   [完成] {spec['label']}                    ")
            except Exception as exc:  # noqa: BLE001
                print(f"\r   [失败] {spec['label']}: {exc}")
                failures += 1

    failures += download_pairs(
        args.root, args.pairs, mirror=args.mirror, connections=args.connections,
        on_file=on_file, on_progress=on_progress)

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
