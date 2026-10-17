"""Stage the MSVC runtime DLLs into the portable build, then verify imports.

onnxruntime's .pyd resolves its dependencies only inside its own directory under
LOAD_WITH_ALTERED_SEARCH_PATH, so shipping msvcp140.dll next to python.exe is not
enough - msvcp140_1.dll and friends must sit in onnxruntime\\capi too. On a machine
without the VC++ redistributable that is the difference between "works" and
"DLL load failed".
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

VC_DLLS = [
    "msvcp140.dll", "msvcp140_1.dll", "msvcp140_2.dll", "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll", "vcruntime140.dll", "vcruntime140_1.dll",
    "concrt140.dll", "vccorlib140.dll",
]


def main() -> int:
    python = ROOT / "runtime" / "python" / "python.exe"
    if not python.is_file():
        print(f"找不到 {python}")
        return 1

    site = Path(subprocess.check_output(
        [str(python), "-c",
         "import sysconfig;print(sysconfig.get_paths()['purelib'])"],
        text=True).strip())
    print(f"site-packages: {site}")

    python_root = ROOT / "runtime" / "python"
    # msvc-runtime drops its DLLs next to the interpreter (and in Scripts), not into
    # site-packages/msvc_runtime, so all three places have to be searched.
    search_dirs = [python_root / "Scripts", python_root, site / "msvc_runtime"]
    available: dict[str, Path] = {}
    for directory in search_dirs:
        if not directory.is_dir():
            continue
        for name in VC_DLLS:
            path = directory / name
            if name not in available and path.is_file():
                available[name] = path

    print(f"找到 {len(available)} 个运行库文件：{sorted(available)}")
    if not available:
        print("没找到任何 VC++ 运行库 DLL —— msvc-runtime 装上了吗？")
        return 1

    # onnxruntime resolves its dependencies only inside its own directory, and the
    # same holds for the other native packages.
    targets = [
        site / "onnxruntime" / "capi",
        site / "onnxruntime",
        site / "cv2",
        site / "rapidocr_onnxruntime",
    ]
    for target in targets:
        if not target.is_dir():
            continue
        copied = 0
        for name, source in available.items():
            try:
                shutil.copy2(source, target / name)
                copied += 1
            except Exception as exc:  # noqa: BLE001
                print(f"  复制 {name} 到 {target.name} 失败：{exc}")
        print(f"  -> {target.relative_to(site)}: 复制 {copied} 个")

    print()
    print("验证导入（这一步失败就说明包不能用）")
    code = (
        "import numpy, mss, PIL, cv2, onnxruntime, rapidocr_onnxruntime, transformers, tkinter\n"
        "print('  numpy        ', numpy.__version__)\n"
        "print('  onnxruntime  ', onnxruntime.__version__)\n"
        "print('  transformers ', transformers.__version__)\n"
        "print('  cv2          ', cv2.__version__)\n"
        "print('  ALL_IMPORTS_OK')\n"
    )
    result = subprocess.run([str(python), "-c", code], capture_output=True,
                            text=True, encoding="utf-8", errors="replace")
    print(result.stdout or "", end="")
    if result.returncode != 0 or "ALL_IMPORTS_OK" not in (result.stdout or ""):
        print(result.stderr or "")
        print("导入失败")
        return 1
    print()
    print("运行库部署完成，关键库全部可导入。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
