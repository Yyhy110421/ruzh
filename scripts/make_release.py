"""Build the folder that gets handed to a customer.

The working folder is a source checkout. It carries the handover notes, the build
scripts, the packaging notes, and whatever the last run wrote into logs/. None of that
belongs in something a customer paid for, and the handover in particular is internal -
it names competitors, quotes measurements and lists the product's weak spots in plain
language.

So the customer folder is built from an allowlist rather than by deleting from a copy.
A file added later then has to be thought about, instead of shipping by accident.

    python scripts/make_release.py                     # build 守译-客户版
    python scripts/make_release.py --out D:\产品        # somewhere else
    python scripts/make_release.py --zip               # also produce a .zip
    python scripts/make_release.py --dry-run           # list what would be copied
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Copied from this folder.
#: "llm" is the local model runtime: llama-server.exe plus a Qwen2.5-1.5B GGUF, about
#: 1.1 GB. It is what makes the offline translation usable ("预付款" instead of
#: "预付款款"), so the customer folder is useless without it.
DIRECTORIES = ["app", "assets", "engines_custom", "models", "llm"]

#: The bundled interpreter is a staging artifact, not a source file: a full CPython install
#: (about 610 MB, 11k files) with the app's dependencies beside it. It is deliberately not
#: in the repository, so it has to be pointed at rather than guessed.
#:
#: Rebuild it with scripts/build_runtime.py - about two minutes, and it needs no network:
#:
#:     python scripts/build_runtime.py --from-venv .venv --out dist/runtime
#:     python scripts/make_release.py --runtime dist/runtime
#:
#: Measured: 114 seconds, 617 MB, 9/9 MSVC DLLs staged, imports verified, and the runtime
#: it produced launched this product's window.
DEFAULT_RUNTIME = ROOT.parent / "守译" / "runtime"

FILES = [
    "main.py",
    "LICENSE",
    "使用说明.txt",
    "启动.bat",
    "启动-调试.bat",
    # The .bat is useless without the script it calls. Shipped alone it produced a
    # customer-facing menu entry that could only error, and the first build to reach
    # anyone had exactly that hole in it.
    "安装桌面快捷方式.bat",
    "make_shortcut.py",
]

#: Never copied, even from inside an allowed directory.
SKIP_NAMES = {"__pycache__", ".pytest_cache", ".git"}
SKIP_SUFFIX = (".pyc", ".pyo")

#: Deliberately not shipped, and why - kept here so the reason survives a later edit.
NOT_SHIPPED = {
    "交接说明.md": "内部交接文档：写了对标、实测数据、已知短板",
    "README.md": "开发者文档：架构、构建、第三方授权细节",
    "requirements.txt": "开发用：客户机器上不装 Python",
    "scripts/": "构建工具：客户不需要，也不该看到",
    "config.json": "客户第一次运行自己生成，默认值就是对的",
    "logs/": "运行日志：开发时留下的",
    "_run.log": "开发时留下的空文件",
}

#: Built inside the project rather than straight onto the desktop, because a Python
#: process started from this session can only write inside the project folder. Move the
#: finished folder wherever it belongs afterwards - it is a plain directory, and moving
#: it on the same drive is instant.
DEFAULT_OUT = ROOT / "dist" / "守译-客户版"


def wanted(rel: Path) -> bool:
    if any(part in SKIP_NAMES for part in rel.parts):
        return False
    if rel.suffix in SKIP_SUFFIX:
        return False
    return True


def collect() -> list[tuple[Path, Path]]:
    """(source, relative destination) for everything that ships."""
    out: list[tuple[Path, Path]] = []
    for name in FILES:
        src = ROOT / name
        if src.is_file():
            out.append((src, Path(name)))
        else:
            print(f"  ⚠ 少了 {name}（应该有的）")
    for name in DIRECTORIES:
        base = ROOT / name
        if not base.is_dir():
            print(f"  ⚠ 少了 {name}/（应该有的）")
            continue
        for item in sorted(base.rglob("*")):
            if not item.is_file():
                continue
            rel = item.relative_to(ROOT)
            if wanted(rel):
                out.append((item, rel))
    return out


def human(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:,.0f} {unit}" if unit == "B" else f"{num:,.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def main() -> int:
    parser = argparse.ArgumentParser(description="打客户版")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    parser.add_argument("--runtime", default=str(DEFAULT_RUNTIME),
                        help="自带 Python 的 runtime/ 目录（构建产物，仓库里没有）")
    parser.add_argument("--zip", action="store_true", help="另外打一个 zip")
    parser.add_argument("--dry-run", action="store_true", help="只列不拷")
    args = parser.parse_args()

    target = Path(args.out).resolve()
    runtime = Path(args.runtime).resolve()
    if not (runtime / "python" / "python.exe").is_file():
        print(f"找不到自带 Python：{runtime}\\python\\python.exe")
        print("用 --runtime 指到一个已有的 runtime 目录。")
        print("它是构建产物（一套完整的 CPython + 依赖），不在仓库里；")
        print("怎么重建，见本文件顶部 DEFAULT_RUNTIME 的说明。")
        return 1

    plan = collect()
    for item in sorted(runtime.rglob("*")):
        if item.is_file() and wanted(Path("runtime") / item.relative_to(runtime)):
            plan.append((item, Path("runtime") / item.relative_to(runtime)))
    total = sum(src.stat().st_size for src, _ in plan)

    print("=" * 72)
    print("客户版内容")
    print("=" * 72)
    by_top: dict[str, int] = {}
    for src, rel in plan:
        key = rel.parts[0]
        by_top[key] = by_top.get(key, 0) + src.stat().st_size
    for key, size in sorted(by_top.items(), key=lambda kv: -kv[1]):
        print(f"  {key:<22} {human(size):>10}")
    print(f"  {'合计':<22} {human(total):>10}   共 {len(plan)} 个文件")
    print()
    print("不带的东西（故意的）")
    for name, why in NOT_SHIPPED.items():
        print(f"  {name:<20} {why}")
    print()

    if args.dry_run:
        return 0

    # The only place it must not go is inside something it is about to copy from, or the
    # walk would pick up its own output. Anywhere else is fine - and it has to be able to
    # be inside the project, because a Python process started from a session with a file
    # sandbox can write nowhere else.
    sources = [ROOT / name for name in DIRECTORIES] + [runtime]
    for source in sources:
        if target == source or source in target.parents:
            print(f"输出目录不能在 {source} 里面，会被自己拷进去")
            return 1
    if target.exists():
        print(f"目标已存在，先删掉它：{target}")
        return 1

    print("=" * 72)
    print(f"拷到 {target}")
    print("=" * 72)
    started = time.perf_counter()
    target.mkdir(parents=True, exist_ok=False)
    for index, (src, rel) in enumerate(plan, start=1):
        dst = target / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        if index % 2000 == 0:
            print(f"  ... {index}/{len(plan)}")
    print(f"  拷完，用时 {time.perf_counter() - started:.1f} 秒")

    # A last look: nothing internal should have come along for the ride.
    leaked = [name.rstrip("/") for name in NOT_SHIPPED
              if (target / name.rstrip("/")).exists()]
    if leaked:
        print(f"  ❗ 漏进去了：{leaked}")
        return 1
    print("  检查过了，内部文件一个都没进去")

    if args.zip:
        archive = target.with_suffix(".zip")
        print(f"  打包 {archive.name} …")
        shutil.make_archive(str(target), "zip", root_dir=target.parent, base_dir=target.name)
        print(f"  打好：{human(archive.stat().st_size)}")

    print()
    print("客户版在这里：")
    print(f"  {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
