"""Ask the Windows shell which icon it resolves for a shortcut.

SHGetFileInfo with SHGFI_ICONLOCATION returns the file and index the shell would
actually draw, which is the only way to tell whether the .lnk's icon is being
honoured or falling back to the generic shortcut icon.
"""
import ctypes
import os
import sys
from ctypes import wintypes

SHGFI_ICON = 0x000000100
SHGFI_ICONLOCATION = 0x000001000
SHGFI_LARGEICON = 0x000000000
SHGFI_SMALLICON = 0x000000001
SHGFI_USEFILEATTRIBUTES = 0x000000010
SHGFI_SHELLICONSIZE = 0x000000004


class SHFILEINFOW(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HICON),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", wintypes.WCHAR * 260),
        ("szTypeName", wintypes.WCHAR * 80),
    ]


def resolve(path: str, flags: int) -> tuple[str, int]:
    info = SHFILEINFOW()
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.SHGetFileInfoW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                                       ctypes.POINTER(SHFILEINFOW), wintypes.UINT, wintypes.UINT]
    shell32.SHGetFileInfoW.restype = ctypes.c_void_p
    shell32.SHGetFileInfoW(path, 0, ctypes.byref(info), ctypes.sizeof(info), flags)
    return info.szDisplayName, info.iIcon


def main() -> int:
    desktop = os.path.join(os.environ["USERPROFILE"], "Desktop")
    lnk = os.path.join(desktop, "守译.lnk")
    ico = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "assets", "app.ico")

    print("shortcut exists :", os.path.isfile(lnk))
    print("icon file exists:", os.path.isfile(ico))
    print("icon abs path   :", ico)
    print("EXPANDED env    :", os.path.expandvars("%USERPROFILE%\\Desktop\\yhy.1\\assets\\app.ico"))
    print()

    for label, flags in (
        ("ICONLOCATION|LARGE", SHGFI_ICONLOCATION | SHGFI_LARGEICON),
        ("ICONLOCATION|SMALL", SHGFI_ICONLOCATION | SHGFI_SMALLICON),
        ("ICON|LARGE         ", SHGFI_ICON | SHGFI_LARGEICON),
    ):
        name, index = resolve(lnk, flags)
        print(f"{label}  ->  file=[{name}]  index={index}")

    # And what the shell says about the .ico itself, to prove it is usable.
    name, index = resolve(ico, SHGFI_ICONLOCATION | SHGFI_LARGEICON)
    print(f"{'ICO direct          '}  ->  file=[{name}]  index={index}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
