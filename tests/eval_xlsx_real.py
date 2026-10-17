"""End-to-end: a real Russian price list through the real offline engine.

The structural test uses a fake translator so it can run in seconds. This one uses the
engine, because the point of translating a quotation is the text a buyer ends up
reading - and a price list is where deduplication earns its keep, since the same label
sits in every row.
"""
import shutil
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "xlsx_real.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.xlsx_io import read_cells, translate_xlsx  # noqa: E402

SOURCE = ROOT / "tests" / "fixtures" / "报价单.xlsx"
WORK = ROOT / "tests" / "xlsx_real_work"
OUTPUT = WORK / "报价单_译文.xlsx"

failures = 0


def check(ok: bool, label: str) -> None:
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")


def sorted_refs(cells: dict) -> list[str]:
    def key(ref: str):
        letters = "".join(c for c in ref if c.isalpha())
        digits = "".join(c for c in ref if c.isdigit())
        return (int(digits or 0), letters)

    return sorted(cells, key=key)


def main() -> int:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)

    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = "opus"

    before = read_cells(SOURCE)

    print("=" * 78)
    print("输入：供应商发来的俄语报价单")
    print("=" * 78)
    for ref in sorted_refs(before):
        print(f"  {ref:<5} {before[ref]}")
    print()

    print("=" * 78)
    print("用离线引擎翻译…")
    print("=" * 78)
    started = time.perf_counter()
    result = translate_xlsx(cfg, cfg.resolved_models_root(), SOURCE, OUTPUT)
    elapsed = time.perf_counter() - started
    print(f"  翻译 {result.strings} 条文字，覆盖 {result.cells} 个单元格，"
          f"用时 {elapsed:.1f} 秒，失败 {len(result.failed)} 处")
    print()

    after = read_cells(OUTPUT)

    print("=" * 78)
    print("输出：译文")
    print("=" * 78)
    for ref in sorted_refs(after):
        print(f"  {ref:<5} {after[ref]}")
    print()

    print("=" * 78)
    print("检查")
    print("=" * 78)
    check(len(result.failed) == 0, "没有失败")
    numbers_before = {r: v for r, v in before.items() if v.replace(".", "").isdigit()}
    check(bool(numbers_before), f"输入里有 {len(numbers_before)} 个数字单元格")
    check(all(after.get(r) == v for r, v in numbers_before.items()),
          "数字单元格一个都没变")
    text_before = {r: v for r, v in before.items()
                   if v.strip() and not v.replace(".", "").isdigit()}
    changed = [r for r, v in text_before.items() if after.get(r) != v]
    check(len(changed) == len(text_before),
          f"全部 {len(text_before)} 个文字单元格都被翻译（实际 {len(changed)} 个）")
    check(set(after) == set(before), "单元格位置一个不多一个不少")
    print()

    print("=" * 78)
    print(f"失败 {failures} 项")
    print(f"输出文件: {OUTPUT}  ({OUTPUT.stat().st_size} 字节)")
    print("=" * 78)
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
