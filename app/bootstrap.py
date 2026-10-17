"""Startup bootstrap: process DPI awareness and native runtime library fixes.

Everything here must run before ``onnxruntime`` (and therefore RapidOCR) is
imported, so the entry points import this module first.
"""

from __future__ import annotations

import ctypes
import os
import shutil
import sys
from pathlib import Path

#: Native DLLs the Microsoft C runtime ships, as staged by the ``msvc-runtime``
#: PyPI package. Windows extension modules loaded from site-packages only search
#: their own directory for dependencies under LOAD_WITH_ALTERED_SEARCH_PATH, so a
#: machine without the VC++ redistributable needs them copied next to the
#: extension modules as well.
VC_RUNTIME_DLLS = (
    "msvcp140.dll",
    "msvcp140_1.dll",
    "msvcp140_2.dll",
    "msvcp140_atomic_wait.dll",
    "msvcp140_codecvt_ids.dll",
    "vcruntime140.dll",
    "vcruntime140_1.dll",
    "vcruntime140_threads.dll",
    "concrt140.dll",
)

_dpi_done = False
_runtime_done = False
_unicode_done = False


def enable_dpi_awareness() -> str:
    """Make Tk, mss and Win32 all agree on physical pixels.

    Without this, screenshots on a scaled display are offset or blurry relative to
    the coordinates Tk reports.
    """
    global _dpi_done
    if _dpi_done or not sys.platform.startswith("win"):
        return "skipped"
    _dpi_done = True

    user32 = ctypes.windll.user32
    # PER_MONITOR_AWARE_V2 == -4
    try:
        if user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return "per-monitor-v2"
    except Exception:  # noqa: BLE001
        pass
    try:
        if ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:  # noqa: BLE001
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except Exception:  # noqa: BLE001
        return "none"


def _runtime_source_dirs() -> list[Path]:
    dirs: list[Path] = []
    prefix = Path(sys.prefix)
    for candidate in (prefix / "Scripts", prefix / "bin", prefix, Path(sys.base_prefix), Path(sys.base_prefix) / "Scripts"):
        if candidate.is_dir() and candidate not in dirs:
            dirs.append(candidate)
    for entry in sys.path:
        pkg = Path(entry) / "msvc_runtime"
        if pkg.is_dir() and pkg not in dirs:
            dirs.append(pkg)
    return dirs


def _native_dirs_needing_runtime() -> list[Path]:
    """Directories holding native extension modules that may need the CRT."""
    targets: list[Path] = []
    for entry in sys.path:
        if not entry:
            continue
        root = Path(entry)
        if not root.is_dir():
            continue
        for sub in ("onnxruntime/capi", "onnxruntime", "cv2", "rapidocr_onnxruntime"):
            candidate = root / sub
            if candidate.is_dir():
                targets.append(candidate)
    return targets


def ensure_vc_runtime(verbose: bool = False) -> list[str]:
    """Guarantee the MSVC runtime is discoverable before native modules load."""
    global _runtime_done
    if _runtime_done or not sys.platform.startswith("win"):
        return []
    _runtime_done = True

    sources = _runtime_source_dirs()
    available: dict[str, Path] = {}
    for directory in sources:
        for name in VC_RUNTIME_DLLS:
            path = directory / name
            if name not in available and path.is_file():
                available[name] = path

    # 1. Make the runtime directories part of the loader search path.
    for directory in sources:
        try:
            os.add_dll_directory(str(directory))
        except Exception:  # noqa: BLE001
            pass
    path_parts = [str(d) for d in sources]
    os.environ["PATH"] = os.pathsep.join(path_parts + [os.environ.get("PATH", "")])

    # 2. Copy anything still missing next to the extension modules themselves.
    notes: list[str] = []
    for target in _native_dirs_needing_runtime():
        for name, source in available.items():
            dest = target / name
            if dest.is_file():
                continue
            try:
                shutil.copy2(source, dest)
                notes.append(f"{name} -> {target}")
            except Exception:  # noqa: BLE001
                pass
    if verbose:
        for note in notes:
            print(f"[bootstrap] staged {note}")
    return notes


def enable_unicode_paths() -> str:
    """Let the tokenizer load when the install path contains non-ASCII characters.

    ``sentencepiece`` is a C++ library that opens the model file through a narrow
    ``std::ifstream``. On Windows that cannot see a path like
    ``...\\\\桌面\\\\守译\\\\models\\\\en-zh\\\\source.spm``: it reports NOT_FOUND for a
    file that plainly exists, and ``AutoTokenizer`` surfaces it as a tokenizer load
    failure. Putting a program in a Chinese-named folder is completely normal here, so
    this is not a corner case.

    sentencepiece can also be fed the serialized model, which avoids the file path
    entirely, so transformers' Marian loader is redirected to that route - **only when
    the path is actually non-ASCII**, leaving the ordinary case untouched. If anything
    about the library looks different than expected, the original function is used.
    """
    global _unicode_done
    if _unicode_done:
        return "already"
    _unicode_done = True

    try:
        import sentencepiece
        from transformers.models.marian import tokenization_marian as marian
    except Exception:  # noqa: BLE001
        return "unavailable"

    original = getattr(marian, "load_spm", None)
    if not callable(original):
        return "unsupported"

    def load_spm(path, sp_model_kwargs):  # noqa: ANN001, ANN202
        try:
            if str(path).isascii():
                return original(path, sp_model_kwargs)
        except Exception:  # noqa: BLE001
            return original(path, sp_model_kwargs)
        processor = sentencepiece.SentencePieceProcessor(**(sp_model_kwargs or {}))
        processor.LoadFromSerializedProto(Path(path).read_bytes())
        return processor

    marian.load_spm = load_spm
    return "patched"


def prepare(verbose: bool = False) -> None:
    """Run every bootstrap step. Safe to call repeatedly."""
    # transformers prints a loud "PyTorch was not found" banner on import; we only use
    # its tokenizers, so keep that noise out of the app log.
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("TRANSFORMERS_NO_ADVISORY_WARNINGS", "1")
    # Models are fetched by app/downloader.py over plain HTTP, so the app never needs
    # the Hugging Face Hub at runtime. Forbidding hub access removes any chance of an
    # import or model load stalling on a network timeout.
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
    os.environ.setdefault("DO_NOT_TRACK", "1")
    enable_dpi_awareness()
    ensure_vc_runtime(verbose=verbose)
    enable_unicode_paths()
