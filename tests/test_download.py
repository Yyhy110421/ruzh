"""Dev test: probe a small and a large model file, then download the small one."""
import os
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import downloader, models  # noqa: E402

BASE = f"{models.DEFAULT_MIRROR}/Xenova/opus-mt-en-zh/resolve/main"


def main() -> int:
    for rel in ("config.json", "onnx/encoder_model_quantized.onnx"):
        url = f"{BASE}/{rel}"
        try:
            size, range_ok = downloader.probe(url)
            print(f"probe {rel:42s} size={size} ({ (size or 0)/1048576:.1f} MB) range_ok={range_ok}")
        except Exception:
            traceback.print_exc()

    dest = os.path.join(ROOT, "tests", "cfg.json")
    try:
        out = downloader.download(f"{BASE}/config.json", dest, connections=2)
        print("small download OK:", os.path.getsize(out), "bytes")
    except Exception:
        traceback.print_exc()
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
