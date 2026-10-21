"""The engines sometimes emit HTML entities instead of characters."""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.pipeline import _strip_entities  # noqa: E402

failures = 0


def check(ok, label):
    global failures
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")
    if not ok:
        failures += 1


print("=" * 74)
print("实体清理")
print("=" * 74)

check(_strip_entities("向买方 & aspos; 仓库") == "向买方 ' 仓库",
      f"& aspos; -> '：{_strip_entities('向买方 & aspos; 仓库')!r}")
check(_strip_entities("a &apos; b") == "a ' b", "&apos; -> '")
check(_strip_entities("a &amp; b") == "a & b", "&amp; -> &")
check(_strip_entities("没有实体") == "没有实体", "没有实体就原样返回")
check(_strip_entities("") == "", "空串安全")
check("&" not in _strip_entities("x &nbsp; y"), "&nbsp; 变成空格")

print()
print("=" * 74)
print(f"失败 {failures} 项")
print("=" * 74)
sys.exit(1 if failures else 0)
