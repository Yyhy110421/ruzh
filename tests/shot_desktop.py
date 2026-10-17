"""Screenshot the desktop so the rendered shortcut icon can be inspected directly."""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import mss  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402


def main() -> int:
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "desktop_shot.png")
    with mss.mss() as sct:
        monitor = sct.monitors[0]
        raw = np.asarray(sct.grab(monitor), dtype=np.uint8)
    img = Image.fromarray(raw[:, :, :3][:, :, ::-1])  # BGRA -> RGB
    img.save(out)
    print(f"virtual screen {monitor['width']}x{monitor['height']} @ "
          f"({monitor['left']}, {monitor['top']}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
