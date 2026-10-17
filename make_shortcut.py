"""Create a desktop shortcut for this portable build.

The shortcut is what makes the folder feel like an installed program: a name, the app
icon, and a double-click that shows no console window. It points at the *bundled*
interpreter, so it keeps working after the folder is moved.

PowerShell is invoked with -EncodedCommand (UTF-16LE + base64) rather than -Command,
because passing a script containing quotes and non-ASCII paths through the command line
gets mangled by cmd.exe and PowerShell 5.1 alike.
"""

from __future__ import annotations

import base64
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SHORTCUT_NAME = "守译"

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass


def powershell(script: str) -> tuple[int, str]:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive",
         "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def main() -> int:
    pythonw = ROOT / "runtime" / "python" / "pythonw.exe"
    main_py = ROOT / "main.py"
    icon = ROOT / "assets" / "app.ico"

    missing = [str(p) for p in (pythonw, main_py) if not p.exists()]
    if missing:
        print("文件夹不完整，找不到：")
        for path in missing:
            print(f"  {path}")
        print("\n请重新完整解压后再试。")
        return 1

    icon_line = f"$lnk.IconLocation = '{icon}'" if icon.exists() else ""
    script = f"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath('Desktop')
$path = Join-Path $desktop '{SHORTCUT_NAME}.lnk'
$lnk = $shell.CreateShortcut($path)
$lnk.TargetPath = '{pythonw}'
$lnk.Arguments = '"{main_py}"'
$lnk.WorkingDirectory = '{ROOT}'
$lnk.Description = '守译：中俄屏幕、文档、图片翻译，全部在本机完成'
{icon_line}
$lnk.Save()
Write-Output "SHORTCUT_OK $path"
"""
    code, output = powershell(script)
    if code != 0 or "SHORTCUT_OK" not in output:
        print("创建快捷方式失败：")
        print(output.strip() or f"（PowerShell 返回 {code}）")
        return 1

    line = next((l for l in output.splitlines() if "SHORTCUT_OK" in l), "")
    print("已创建桌面快捷方式：")
    print("  " + line.replace("SHORTCUT_OK ", "").strip())
    print()
    print("现在桌面上会有一个「守译」图标，双击即可运行。")
    if not icon.exists():
        print("（提示：没找到 assets/app.ico，快捷方式用的是默认图标。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
