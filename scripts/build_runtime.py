"""Build the bundled interpreter the product ships with.

The folder a customer receives carries its own Python - about 550 MB of interpreter plus
dependencies - so that nothing has to be installed on their machine. That folder is a
build artifact and is deliberately not in the repository.

Nothing recorded how to make it either, which meant the one build that actually reached a
customer could not be reproduced from the source tree. This is that recipe:

    python scripts/build_runtime.py                    # build into dist/runtime
    python scripts/build_runtime.py --out runtime      # or straight into place
    python scripts/build_runtime.py --python C:\\Python312\\python.exe

Three steps: copy a CPython install, install requirements.txt into it using its own pip,
then run stage_runtime.py next to it - which puts the MSVC DLLs where onnxruntime looks
for them and proves the result imports.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
STAGE_SCRIPT = ROOT / "stage_runtime.py"

#: Copied from the source interpreter and then filled in by pip.
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache"}

#: The Microsoft C++ runtime, which the native wheels need and a fresh Windows install
#: does not have. The msvc-runtime package drops these next to the interpreter and in
#: Scripts, which is why copying site-packages alone is not enough - measured: a runtime
#: built from site-packages only had 8 of the 9 somewhere in the tree, and importing
#: succeeded here purely because this machine happens to have the redistributable
#: installed. On a customer's machine that is "DLL load failed" on first launch.
#:
#: The same list stage_runtime.py deploys; kept in step by hand because that script ships
#: inside the product and this one does not.
VC_DLLS = [
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "concrt140.dll", "vccorlib140.dll",
]


def human(num: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:,.0f} {unit}" if unit == "B" else f"{num:,.1f} {unit}"
        num /= 1024
    return f"{num:.1f} GB"


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def site_packages(python: Path) -> Path:
    """Where a given interpreter puts its packages.

    Asked rather than assumed: the answer is Lib\\site-packages for an installed CPython
    and Lib\\site-packages inside the venv for a virtual one, and the copied interpreter
    may be either. Same call stage_runtime.py makes.
    """
    output = subprocess.check_output(
        [str(python), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
        text=True, encoding="utf-8", errors="replace").strip()
    return Path(output)


def source_prefix(explicit: str | None) -> Path:
    """The install directory of the interpreter to copy."""
    if explicit:
        exe = Path(explicit).resolve()
        if exe.is_dir():
            exe = exe / ("python.exe" if sys.platform == "win32" else "python3")
        if not exe.is_file():
            raise SystemExit(f"找不到解释器：{exe}")
        return exe.parent
    # sys.base_prefix rather than sys.prefix: run this from the project's own virtual
    # environment and sys.prefix points at the venv, which is not relocatable.
    prefix = Path(sys.base_prefix).resolve()
    if not (prefix / ("python.exe" if sys.platform == "win32" else "bin/python3")).exists():
        raise SystemExit(f"{prefix} 看起来不是完整的 Python 安装，用 --python 指一个")
    return prefix


def main() -> int:
    parser = argparse.ArgumentParser(description="造出自带的 Python 运行时")
    parser.add_argument("--out", default=str(ROOT / "dist" / "runtime"),
                        help="runtime 目录（里面会有 python\\python.exe）")
    parser.add_argument("--python", default=None, help="要拷贝的 Python（默认当前解释器的安装目录）")
    parser.add_argument("--from-venv", default=None, metavar="VENV",
                        help="从这个已有的虚拟环境复制依赖，不走网络。"
                             "推荐：pip 装 74 个包要走几百 MB 流量，而且会失败")
    parser.add_argument("--index", default=None, metavar="URL",
                        help="pip 源（不用 --from-venv 时才有意义）")
    parser.add_argument("--jobs", default="0", help="pip 并发数，0 = 默认")
    parser.add_argument("--keep-going", action="store_true",
                        help="pip 装失败也继续（stage 那步会验出来）")
    args = parser.parse_args()

    out = Path(args.out).resolve()
    python_dir = out / "python"
    exe_name = "python.exe" if sys.platform == "win32" else "python3"

    prefix = source_prefix(args.python)
    print("=" * 72)
    print("自带运行时")
    print("=" * 72)
    print(f"  源解释器   {prefix}")
    print(f"  目标       {python_dir}")
    print()

    if python_dir.exists():
        print(f"{python_dir} 已经存在。先删掉它再来，或者换个 --out。")
        return 1
    if not REQUIREMENTS.is_file():
        print(f"找不到 {REQUIREMENTS}")
        return 1

    started = time.perf_counter()

    print("[1/3] 拷贝解释器")
    python_dir.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        prefix, python_dir,
        ignore=shutil.ignore_patterns(*SKIP_DIRS),
        symlinks=False,
    )
    print(f"      拷完 {human(folder_size(python_dir))}，"
          f"用时 {time.perf_counter() - started:.1f} 秒")

    bundled = python_dir / exe_name
    if not bundled.is_file():
        print(f"      拷完却没有 {bundled}，源目录结构不对")
        return 1

    print("[2/3] 装依赖")
    site = site_packages(bundled)
    print(f"      目标 site-packages: {site}")
    deps_started = time.perf_counter()

    if args.from_venv:
        # Copying the packages out of an environment that already has them is the same
        # result as installing them, without the network. Measured here: pip needed
        # several hundred MB of downloads for 74 packages and did not finish at all,
        # while this takes seconds and works offline - which is what a build recipe for
        # a shipped product should do.
        source = site_packages(Path(args.from_venv) / ("Scripts" if sys.platform == "win32" else "bin") / ("python.exe" if sys.platform == "win32" else "python3"))
        print(f"      从 {source} 复制")
        copied = 0
        for item in source.iterdir():
            target = site / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True,
                                ignore=shutil.ignore_patterns(*SKIP_DIRS))
            else:
                shutil.copy2(item, target)
            copied += 1
        print(f"      复制 {copied} 项，用时 {time.perf_counter() - deps_started:.1f} 秒")
    else:
        command = [str(bundled), "-m", "pip", "install", "--no-cache-dir",
                   "--disable-pip-version-check", "-r", str(REQUIREMENTS)]
        if args.index:
            command += ["-i", args.index]
        if args.jobs and args.jobs != "0":
            command += ["--jobs", str(args.jobs)]
        result = subprocess.run(command, text=True, encoding="utf-8", errors="replace")
        if result.returncode != 0:
            print(f"      pip 退出码 {result.returncode}")
            if not args.keep_going:
                print("      装依赖失败。换个源试试 --index，或者用 --from-venv 直接复制。")
                return 1
        else:
            print(f"      装完，用时 {time.perf_counter() - deps_started:.1f} 秒")
    print(f"      现在 {human(folder_size(python_dir))}")

    if args.from_venv:
        print("      [2b] 搬 VC++ 运行库")
        venv = Path(args.from_venv).resolve()
        sources = [venv / "Scripts", venv, site]
        placed: set[str] = set()
        for name in VC_DLLS:
            for where in sources:
                candidate = where / name
                if candidate.is_file():
                    # Both places, because a DLL's own directory is what gets searched:
                    # the interpreter's for python itself, Scripts for anything started
                    # through pythonw.
                    for target_dir in (python_dir, python_dir / "Scripts"):
                        target_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(candidate, target_dir / name)
                    placed.add(name)
                    break
        print(f"      放好 {len(placed)}/{len(VC_DLLS)} 个：{sorted(placed)}")
        if len(placed) < len(VC_DLLS):
            missing = sorted(set(VC_DLLS) - placed)
            print(f"      少了 {missing} —— 从源环境里找不到，客户机器上可能起不来")

    print("[3/3] 放 VC++ 运行库并验证导入")
    # stage_runtime.py resolves the runtime relative to itself, so it has to sit next to
    # the runtime folder - which is exactly where it sits in the shipped product.
    staged = out.parent / STAGE_SCRIPT.name
    shutil.copy2(STAGE_SCRIPT, staged)
    stage = subprocess.run([str(bundled), str(staged)], text=True,
                           encoding="utf-8", errors="replace")
    if stage.returncode != 0:
        print("      stage_runtime.py 没过，这个运行时不能用")
        return 1

    print()
    print("=" * 72)
    print(f"好了：{python_dir}")
    print(f"总大小 {human(folder_size(out))}，"
          f"总用时 {time.perf_counter() - started:.1f} 秒")
    print()
    print("现在可以用它打客户版：")
    print(f"  python scripts/make_release.py --runtime {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
