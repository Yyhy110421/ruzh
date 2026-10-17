"""Generate the application icon.

Two overlapping speech bubbles on a dark rounded square: "something said, and the
same thing in another language".

The .ico is written by hand rather than by Pillow's ICO writer, for two reasons that
both break Explorer's desktop rendering:

* Pillow stores *every* size as a PNG payload. Windows only accepts PNG inside an ICO
  for the 256x256 entry; smaller entries must be BMP/DIB. An all-PNG icon makes
  Explorer fall back to the generic file icon (while DrawIconEx still renders it, so
  the mistake is easy to miss).
* Pillow writes ``wPlanes = 0``; the spec wants 1.

    python scripts/make_icon.py
"""

from __future__ import annotations

import os
import struct
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

#: Master size; every .ico entry is a LANCZOS downscale of this.
MASTER = 1024

BG = (24, 24, 27, 255)          # #18181B, the app's own background
BORDER = (63, 63, 70, 255)      # #3F3F46
SOURCE = (228, 228, 231, 255)   # #E4E4E7, the "spoken" bubble
TARGET = (74, 222, 128, 255)    # #4ADE80, the "translated" bubble

#: Sizes stored as BMP/DIB (what Explorer needs), plus 256 as PNG (the convention).
DIB_SIZES = [16, 24, 32, 48, 64, 128]
PNG_SIZE = 256


def bubble(draw: ImageDraw.ImageDraw, box, colour, radius, tail: str) -> None:
    """A rounded rectangle plus a small tail so it reads as a speech bubble."""
    x0, y0, x1, y1 = box
    draw.rounded_rectangle(box, radius=radius, fill=colour)
    if tail == "left":
        draw.polygon([(x0 + 70, y1 - 30), (x0 + 210, y1 - 30), (x0 + 70, y1 + 130)], fill=colour)
    else:
        draw.polygon([(x1 - 210, y1 - 30), (x1 - 70, y1 - 30), (x1 - 70, y1 + 130)], fill=colour)


def build_master() -> Image.Image:
    img = Image.new("RGBA", (MASTER, MASTER), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    margin = 56
    draw.rounded_rectangle(
        [margin, margin, MASTER - margin, MASTER - margin],
        radius=210, fill=BG, outline=BORDER, width=12,
    )

    # The two bubbles overlap so the icon still reads at 16 px.
    bubble(draw, (196, 232, 592, 566), SOURCE, 88, "left")
    bubble(draw, (432, 428, 828, 726), TARGET, 88, "right")

    # A short "text" line inside each bubble hints at language without needing glyphs,
    # which would be unreadable at small sizes.
    draw.rounded_rectangle([268, 330, 520, 372], radius=21, fill=BG)
    draw.rounded_rectangle([268, 418, 440, 460], radius=21, fill=BG)
    draw.rounded_rectangle([504, 522, 756, 564], radius=21, fill=BG)
    draw.rounded_rectangle([504, 610, 676, 652], radius=21, fill=BG)

    return img


def dib_payload(image: Image.Image) -> bytes:
    """A 32-bit BMP body: BITMAPINFOHEADER + bottom-up BGRA + a zeroed AND mask."""
    width, height = image.size
    header = struct.pack(
        "<IiiHHIIiiII",
        40,            # biSize
        width,         # biWidth
        height * 2,    # biHeight: XOR image + AND mask stacked
        1,             # biPlanes
        32,            # biBitCount
        0,             # biCompression = BI_RGB
        width * height * 4,
        0, 0, 0, 0,
    )

    pixels = image.load()
    rows = []
    for y in range(height - 1, -1, -1):  # BMP rows are bottom-up
        row = bytearray()
        for x in range(width):
            r, g, b, a = pixels[x, y]
            row += bytes((b, g, r, a))   # BGRA
        rows.append(bytes(row))
    xor_bitmap = b"".join(rows)

    # 1bpp mask, each row padded to a 4-byte boundary. All zero: transparency comes
    # from the alpha channel of the 32-bit XOR image.
    mask_row = ((width + 31) // 32) * 4
    mask = b"\x00" * (mask_row * height)

    return header + xor_bitmap + mask


def png_payload(image: Image.Image) -> bytes:
    from io import BytesIO

    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def write_ico(path: str, master: Image.Image) -> None:
    entries: list[tuple[int, bytes]] = []
    for size in DIB_SIZES:
        entries.append((size, dib_payload(master.resize((size, size), Image.LANCZOS))))
    entries.append((PNG_SIZE, png_payload(master.resize((PNG_SIZE, PNG_SIZE), Image.LANCZOS))))

    header = struct.pack("<HHH", 0, 1, len(entries))  # reserved, type=icon, count
    offset = len(header) + 16 * len(entries)

    directory = bytearray()
    body = bytearray()
    for size, payload in entries:
        directory += struct.pack(
            "<BBBBHHII",
            size if size < 256 else 0,   # bWidth (0 means 256)
            size if size < 256 else 0,   # bHeight
            0,                           # bColorCount (0 for 32bpp)
            0,                           # bReserved
            1,                           # wPlanes - Pillow writes 0 here, which is wrong
            32,                          # wBitCount
            len(payload),
            offset,
        )
        body += payload
        offset += len(payload)

    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(directory)
        fh.write(body)


def verify(path: str) -> bool:
    """Re-read the file and assert the layout Explorer requires."""
    with open(path, "rb") as fh:
        data = fh.read()
    reserved, kind, count = struct.unpack_from("<HHH", data, 0)
    ok = reserved == 0 and kind == 1 and count == len(DIB_SIZES) + 1
    for index in range(count):
        off = 6 + index * 16
        width, height, _colours, _res, planes, bits, length, image_off = struct.unpack_from(
            "<BBBBHHII", data, off)
        size = width or 256
        magic = data[image_off:image_off + 4]
        is_png = magic == b"\x89PNG"
        expected_png = size == PNG_SIZE
        status = "ok" if (planes == 1 and bits == 32 and is_png == expected_png) else "BAD"
        if status == "BAD":
            ok = False
        print(f"    {size:3}x{size:<3} {'PNG' if is_png else 'BMP'}  planes={planes} "
              f"bits={bits}  {length:6} bytes  [{status}]")
    return ok


def main() -> int:
    os.makedirs(ASSETS, exist_ok=True)
    master = build_master()

    ico_path = os.path.join(ASSETS, "app.ico")
    write_ico(ico_path, master)

    png_path = os.path.join(ASSETS, "app_256.png")
    master.resize((256, 256), Image.LANCZOS).save(png_path, format="PNG")

    print(f"icon -> {ico_path}  ({os.path.getsize(ico_path):,} bytes)")
    good = verify(ico_path)
    print(f"preview -> {png_path}")
    if not good:
        print("ICON STRUCTURE INVALID", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
