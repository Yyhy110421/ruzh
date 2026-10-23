"""Known model garbling gets repaired; everything else is untouched."""
import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.translator import repair_output  # noqa: E402

failures = 0


def check(ok, label):
    global failures
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}")
    if not ok:
        failures += 1


print("=" * 74)
print("输出修补")
print("=" * 74)

cases = [
    ("付款之日起45个历日的交付", "付款之日起45个日历日的交付", "历日 -> 日历日"),
    ("向买方方 ' 仓库的运费", "向买方 ' 仓库的运费", "买方方 -> 买方"),
    ("向卖方方支付的款项", "向卖方支付的款项", "卖方方 -> 卖方"),
    ("货物应合格技术条例的要求", "货物应符合技术条例的要求", "应合格 -> 应符合"),
    ("合同总额600 000卢布，包括增值税", "合同总额600 000卢布，包括增值税", "干净的译文不动"),
    ("谢谢，常常，刚刚", "谢谢，常常，刚刚", "叠字真词不动"),
    ("", "", "空串安全"),
    ("预付款 30 百分比在 5 银行工作日内", "预付款 30%在 5 银行工作日内", "百分比 -> %"),
    ("百分之30", "30%", "百分之N -> N%"),
    ("合同总金额为600,000卢布", "合同总金额为600 000卢布", "千分位逗号 -> 空格"),
    ("1,234,567元", "1 234 567元", "连着两个逗号也处理"),
    ("价格 600,50 元", "价格 600,50 元", "小数逗号不动"),
]
for source, expected, label in cases:
    got = repair_output(source)
    check(got == expected, f"{label}：{got!r}")

print()
print("=" * 74)
print(f"失败 {failures} 项")
print("=" * 74)
sys.exit(1 if failures else 0)
