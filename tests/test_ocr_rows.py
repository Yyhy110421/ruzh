"""OCR boxes on one horizontal row must come back as one line.

Reported: "翻译上面那一段应该是横着的但是翻译出来的是竖着的". A menu bar - File  View
Repository  Branch  Help - is five boxes at the same y, and treating each as its own unit
turned a horizontal menu into a vertical list of single words.

Run: python tests/test_ocr_rows.py
"""
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "ocr_rows.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.ocr_engine import OcrLine  # noqa: E402
from app.pipeline import _ocr_rows  # noqa: E402

failures = 0


def check(ok, label, extra=""):
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


def box(text, top, left, width=60, height=18):
    return OcrLine(text=text, score=0.9,
                   box=[[left, top], [left + width, top],
                        [left + width, top + height], [left, top + height]],
                   top=top, left=left)


def main() -> int:
    print("=" * 78)
    print("一、横向菜单：同一行的 5 个框 -> 一行")
    print("=" * 78)
    menu = [box("File", 10, 10), box("View", 10, 80), box("Repository", 10, 150, 110),
            box("Branch", 10, 280), box("Help", 10, 360)]
    rows = _ocr_rows(menu)
    check(len(rows) == 1, f"并成 1 行：{len(rows)}", f"{rows}")
    check(rows and rows[0] == "File View Repository Branch Help",
          f"顺序按从左到右：{rows[0] if rows else None!r}")
    print()

    print("=" * 78)
    print("二、纵向的多行不能被并掉")
    print("=" * 78)
    column = [box("File", 10, 10), box("View", 46, 10), box("Repository", 82, 10),
              box("Branch", 118, 10), box("Help", 154, 10)]
    rows = _ocr_rows(column)
    check(len(rows) == 5, f"还是 5 行：{len(rows)}", f"{rows}")
    print()

    print("=" * 78)
    print("三、y 有几个像素抖动也算同一行")
    print("=" * 78)
    jittery = [box("File", 10, 10), box("View", 13, 80), box("Repository", 8, 150)]
    rows = _ocr_rows(jittery)
    check(len(rows) == 1, f"±3px 抖动仍并成 1 行：{len(rows)}", f"{rows}")
    print()

    print("=" * 78)
    print("四、菜单 + 正文混排")
    print("=" * 78)
    mixed = [box("File", 10, 10), box("View", 10, 80), box("Help", 10, 150),
             box("Let's get started!", 60, 10, 400, 30),
             box("Add a repository to GitHub Desktop", 110, 10, 500, 22)]
    rows = _ocr_rows(mixed)
    check(len(rows) == 3, f"菜单 1 行 + 正文 2 行 = 3：{len(rows)}", f"{rows}")
    check(rows[0] == "File View Help", f"第一行是菜单：{rows[0]!r}")
    print()

    print("=" * 78)
    print("五、空输入 / 没有 box 的行")
    print("=" * 78)
    check(_ocr_rows([]) == [], "空列表 -> 空")
    check(_ocr_rows([OcrLine(text="  ", score=1.0)]) == [], "全空白 -> 空")
    no_box = [OcrLine(text="alpha", score=1.0, top=10.0, left=0.0),
              OcrLine(text="beta", score=1.0, top=10.0, left=100.0)]
    rows = _ocr_rows(no_box)
    check(len(rows) == 1, f"没有 box 也能按 top/left 归行：{len(rows)}", f"{rows}")
    print()

    print("=" * 78)
    print(f"失败 {failures} 项")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
