"""Create the virtual environment and install everything the app needs.

Run with any Python 3.10+ interpreter:

    python scripts/setup_env.py            # install / repair the environment
    python scripts/setup_env.py --models   # also download the default models

Besides ``pip install`` this performs one repair that matters on Windows: the
native wheels (onnxruntime, opencv, tokenizers) need the Microsoft C++ runtime.
Instead of requiring the VC++ redistributable, the DLLs shipped by the
``msvc-runtime`` PyPI package are copied next to ``onnxruntime.dll``, because a
DLL's own directory - not the interpreter's - is searched for its dependencies.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# bootstrap.ps1 switches the console to UTF-8, so emit UTF-8 rather than letting
# Python fall back to the GBK locale encoding (which shows up as mojibake).
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

ROOT = Path(__file__).resolve().parent.parent
VENV = ROOT / ".venv"
REQUIREMENTS = ROOT / "requirements.txt"
VC_DLLS = [
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "vcruntime140_threads.dll", "concrt140.dll",
]


def venv_python() -> Path:
    if os.name == "nt":
        return VENV / "Scripts" / "python.exe"
    return VENV / "bin" / "python"


def run(cmd: list[str], **kwargs) -> int:
    print("  $", " ".join(str(part) for part in cmd), flush=True)
    return subprocess.call([str(part) for part in cmd], **kwargs)


def venv_is_usable(py: Path) -> bool:
    """A venv copied from another machine *looks* fine but is dead weight.

    Its ``Scripts\\python.exe`` exists, yet it only redirects to the base interpreter
    recorded in ``pyvenv.cfg`` - a path that belongs to the machine it was built on.
    Nor does ``Lib`` contain the standard library, so nothing can run without that
    base install. Checking that the interpreter actually starts is the only reliable
    test, and it is what stops a shared copy from poisoning the install.
    """
    if not py.is_file():
        return False
    try:
        result = subprocess.run([str(py), "-c", "import sys;print(sys.prefix)"],
                                capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=30)
    except Exception:  # noqa: BLE001
        return False
    if result.returncode != 0:
        return False
    reported = (result.stdout or "").strip().replace("/", "\\").lower()
    return reported == str(VENV).replace("/", "\\").lower()


def ensure_venv() -> Path:
    py = venv_python()
    if py.is_file():
        if venv_is_usable(py):
            print(f"[1/5] 已存在可用的虚拟环境：{VENV}")
            return py
        print(f"[1/5] 检测到 {VENV} 不可用（多为从别的电脑拷来的残留），将重建")
        shutil.rmtree(VENV, ignore_errors=True)

    print(f"[1/5] 创建虚拟环境：{VENV}")
    code = run([sys.executable, "-m", "venv", str(VENV)])
    if code != 0 or not venv_python().is_file():
        raise SystemExit("创建虚拟环境失败。请确认已安装 Python 3.10 及以上版本。")
    return venv_python()


def install(py: Path) -> None:
    print("[2/5] 升级 pip")
    run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
         "--upgrade", "pip"])
    print("[3/5] 安装依赖（首次约 150 MB，请耐心等待）")
    code = run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-input",
                "-r", str(REQUIREMENTS)])
    if code != 0:
        raise SystemExit("依赖安装失败，请检查网络后重试。")


def stage_runtime_dlls(py: Path) -> int:
    """Copy the MSVC runtime next to the native extension modules."""
    print("[4/5] 部署 VC++ 运行库")
    site = Path(subprocess.check_output(
        [str(py), "-c", "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
        text=True,
    ).strip())

    sources: list[Path] = []
    for candidate in (site / "msvc_runtime", VENV / ("Scripts" if os.name == "nt" else "bin")):
        if candidate.is_dir():
            sources.append(candidate)
    available = {}
    for directory in sources:
        for name in VC_DLLS:
            path = directory / name
            if name not in available and path.is_file():
                available[name] = path

    if not available:
        print("     未找到 msvc-runtime 提供的 DLL，跳过（系统可能已装 VC++ 运行库）")
        return 0

    targets = [site / "onnxruntime" / "capi", site / "onnxruntime", site / "cv2"]
    copied = 0
    for target in targets:
        if not target.is_dir():
            continue
        for name, source in available.items():
            dest = target / name
            try:
                shutil.copy2(source, dest)
                copied += 1
            except Exception:  # noqa: BLE001
                pass
    print(f"     复制了 {copied} 个运行库文件")
    return copied


def verify(py: Path) -> bool:
    print("[5/5] 自检")
    script = (
        "import sys\n"
        "mods = ['numpy', 'mss', 'PIL', 'cv2', 'onnxruntime', 'rapidocr_onnxruntime', 'transformers']\n"
        "bad = []\n"
        "for m in mods:\n"
        "    try:\n"
        "        __import__(m)\n"
        "        print(f'  OK   {m}')\n"
        "    except Exception as e:\n"
        "        bad.append(m)\n"
        "        print(f'  FAIL {m}: {e}')\n"
        "sys.exit(1 if bad else 0)\n"
    )
    env = dict(os.environ)
    # transformers prints a loud banner when torch is absent; we only need tokenizers.
    env.setdefault("TRANSFORMERS_VERBOSITY", "error")
    env.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
    code = subprocess.call([str(py), "-c", script], env=env)
    return code == 0


def download_models(py: Path) -> None:
    print("[模型] 下载离线翻译模型")
    run([py, str(ROOT / "scripts" / "download_models.py"), "en-zh", "zh-en"])


def main() -> int:
    global VENV
    parser = argparse.ArgumentParser(description="安装/修复运行环境")
    parser.add_argument("--models", action="store_true", help="安装后顺便下载翻译模型")
    parser.add_argument("--skip-install", action="store_true", help="只做运行库部署与自检")
    parser.add_argument("--venv", default=None,
                        help="虚拟环境目录（默认 .venv，可用于建一个全新环境做验证）")
    args = parser.parse_args()

    if args.venv:
        VENV = Path(args.venv)
        if not VENV.is_absolute():
            VENV = ROOT / VENV

    if os.name != "nt":
        print("提示：本程序针对 Windows 开发，其它系统上截屏与悬浮窗可能不可用。")

    py = ensure_venv()
    if not args.skip_install:
        install(py)
    stage_runtime_dlls(py)
    ok = verify(py)

    if args.models:
        download_models(py)

    print()
    if ok:
        print("完成。双击「启动.bat」即可运行。")
        if not args.models:
            print("提示：首次使用请在程序内点击「下载模型」，或运行 "
                  "python scripts/download_models.py")
        return 0
    print("自检未通过，请查看上面的 FAIL 行，或运行 python scripts/doctor.py 获取详情。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
