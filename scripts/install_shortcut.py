"""Install the desktop shortcut for the app.

The icon must **not** live inside this project directory. On a machine where the
workspace carries restrictive sandbox ACLs, Explorer refuses to extract icons from
files under it and silently falls back to the generic white-page icon - while
``SHGetFileInfo`` and Tk both load the very same file happily, so the problem is
invisible to every check except looking at the desktop.

So the icon is copied to ``%LOCALAPPDATA%\\ScreenTranslator\\app.ico`` and the
shortcut points there. That location is outside the workspace and works.

    python scripts/install_shortcut.py            # install / refresh
    python scripts/install_shortcut.py --remove   # delete the shortcut
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_ICON = ROOT / "assets" / "app.ico"
INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", ROOT)) / "ScreenTranslator"
INSTALLED_ICON = INSTALL_DIR / "app.ico"

SHORTCUT_NAME = "守译"
DESCRIPTION = "守译 - 中俄屏幕、文档、图片翻译，全部在本机完成"


def desktop_dir() -> Path:
    """The real Desktop, honouring a possible OneDrive redirection."""
    try:
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(260)
        # CSIDL_DESKTOPDIRECTORY = 0x0010
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0:
            return Path(buf.value)
    except Exception:  # noqa: BLE001
        pass
    return Path(os.environ["USERPROFILE"]) / "Desktop"


def stage_icon() -> Path:
    """Copy the icon outside the workspace, tolerating a file Explorer has open.

    Once Explorer has rendered the icon it keeps the file open, so overwriting it
    raises PermissionError. An identical file needs no copy at all; a genuinely
    changed one falls back to a content-addressed name, which also gives Explorer a
    fresh cache key.
    """
    import hashlib

    if not SOURCE_ICON.is_file():
        raise SystemExit(f"缺少图标文件：{SOURCE_ICON}\n请先运行 python scripts/make_icon.py")
    digest = hashlib.md5(SOURCE_ICON.read_bytes()).hexdigest()
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    if INSTALLED_ICON.is_file():
        existing = hashlib.md5(INSTALLED_ICON.read_bytes()).hexdigest()
        if existing == digest:
            return INSTALLED_ICON

    try:
        shutil.copy2(SOURCE_ICON, INSTALLED_ICON)
        return INSTALLED_ICON
    except PermissionError:
        alternative = INSTALL_DIR / f"app-{digest[:8]}.ico"
        shutil.copy2(SOURCE_ICON, alternative)
        print(f"（app.ico 被占用，改用 {alternative.name}）")
        # Drop superseded copies so the folder does not grow forever.
        for stale in INSTALL_DIR.glob("app-*.ico"):
            if stale != alternative:
                try:
                    stale.unlink()
                except OSError:
                    pass
        return alternative


def pythonw() -> Path:
    exe = ROOT / ".venv" / "Scripts" / "pythonw.exe"
    if not exe.is_file():
        exe = ROOT / ".venv" / "Scripts" / "python.exe"
    if not exe.is_file():
        raise SystemExit("找不到 .venv 里的解释器，请先运行 install.bat")
    return exe


def make_shortcut(icon: Path) -> Path:
    link = desktop_dir() / f"{SHORTCUT_NAME}.lnk"
    target = pythonw()
    script = (
        "$s = New-Object -ComObject WScript.Shell;"
        f"$l = $s.CreateShortcut('{link}');"
        f"$l.TargetPath = '{target}';"
        f"$l.Arguments = '\"{ROOT / 'main.py'}\"';"
        f"$l.WorkingDirectory = '{ROOT}';"
        f"$l.IconLocation = '{icon},0';"
        f"$l.Description = '{DESCRIPTION}';"
        "$l.Save();"
        "$c = $s.CreateShortcut('" + str(link) + "');"
        "Write-Output $c.IconLocation"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if result.returncode != 0:
        raise SystemExit(f"创建快捷方式失败：{result.stderr.strip()}")
    print(f"快捷方式 -> {link}")
    print(f"  图标   -> {result.stdout.strip()}")
    return link


def refresh_shell() -> None:
    """Ask Explorer to rebuild its icon cache so the new icon shows up."""
    try:
        import ctypes

        SHCNE_ASSOCCHANGED = 0x08000000
        ctypes.windll.shell32.SHChangeNotify(SHCNE_ASSOCCHANGED, 0, None, None)
    except Exception:  # noqa: BLE001
        pass


def verify(icon: Path) -> bool:
    """Confirm the shell resolves the shortcut's icon to the staged file."""
    import ctypes
    from ctypes import wintypes

    class SHFILEINFOW(ctypes.Structure):
        _fields_ = [("hIcon", wintypes.HICON), ("iIcon", ctypes.c_int),
                    ("dwAttributes", wintypes.DWORD),
                    ("szDisplayName", wintypes.WCHAR * 260),
                    ("szTypeName", wintypes.WCHAR * 80)]

    link = desktop_dir() / f"{SHORTCUT_NAME}.lnk"
    info = SHFILEINFOW()
    shell32 = ctypes.WinDLL("shell32")
    shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
    shell32.SHGetFileInfoW(str(link), 0, ctypes.byref(info), ctypes.sizeof(info), 0x000001000)
    resolved = Path(info.szDisplayName)
    print(f"  Shell 解析到的图标 -> {resolved}")
    ok = resolved.resolve() == icon.resolve()
    inside_workspace = str(resolved).lower().startswith(str(ROOT).lower())
    if inside_workspace:
        print("  [警告] 图标位于工作区内，Explorer 会显示成白纸图标", file=sys.stderr)
        ok = False
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="安装桌面快捷方式")
    parser.add_argument("--remove", action="store_true", help="删除快捷方式")
    args = parser.parse_args()

    link = desktop_dir() / f"{SHORTCUT_NAME}.lnk"
    if args.remove:
        if link.is_file():
            link.unlink()
            print(f"已删除 {link}")
        refresh_shell()
        return 0

    icon = stage_icon()
    print(f"图标已部署 -> {icon}  ({icon.stat().st_size:,} bytes)")
    make_shortcut(icon)
    refresh_shell()
    if not verify(icon):
        print("验证未通过，桌面上可能仍显示为通用图标。", file=sys.stderr)
        return 1
    print("完成。若桌面仍显示旧图标，在桌面按 F5。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
