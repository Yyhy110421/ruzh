"""Dev test: load the en-zh ONNX model and translate a few lines."""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

# Write everything to a UTF-8 log so Windows console codepage issues cannot
# corrupt the traceback we actually need to read.
_LOG = open(os.path.join(ROOT, "tests", "translate_out.log"), "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare(verbose=True)

from app import models  # noqa: E402
from app.translator import Translator  # noqa: E402

SENTENCES = [
    "Hello world.",
    "The quick brown fox jumps over the lazy dog.",
    "I don't think we should go there tonight.",
    "Please remember to save your work before closing the application.",
]


def main() -> int:
    root = os.path.join(ROOT, "models")
    src, tgt = "en", "zh"
    if not models.is_installed(root, src, tgt):
        print("[FAIL] model not installed:", models.missing_files(root, src, tgt))
        return 1

    model_dir = models.cache_dir(root, src, tgt)
    print("model dir:", model_dir)

    translator = Translator(model_dir)
    t0 = time.time()
    translator.load()
    print(f"loaded in {time.time() - t0:.2f}s")
    print(translator.describe())
    print("-" * 60)

    for text in SENTENCES:
        try:
            t0 = time.time()
            out = translator.translate(text)
            dt = (time.time() - t0) * 1000
            print(f"{dt:7.0f} ms  {text}\n           -> {out}")
        except Exception:
            traceback.print_exc()
            return 1

    print("-" * 60)
    t0 = time.time()
    out = translator.translate(SENTENCES[1])
    print(f"cache hit: {(time.time() - t0) * 1000:.2f} ms -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
