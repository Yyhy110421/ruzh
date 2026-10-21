"""Run every test, and show the output of the ones that fail.

Each test writes its detail into tests/<name>.log and only its exit code was ever looked
at, which made a failure a guess: you knew something broke but not what. This runs them
one at a time, keeps stdout and stderr, and prints a summary plus the tail of every failure.

    python scripts/run_tests.py            # everything
    python scripts/run_tests.py window     # only tests whose name contains "window"

Exit code is 0 when everything passed.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYTHON = sys.executable
#: How much of a failing test's output to show. Enough for a traceback, not so much that a
#: chatty test buries the summary.
TAIL_LINES = 40


def main(argv: list[str]) -> int:
    pattern = argv[0] if argv else ""
    tests = sorted(p for p in (ROOT / "tests").glob("test_*.py") if pattern in p.name)
    if not tests:
        print(f"没有匹配 {pattern!r} 的测试")
        return 1

    print("=" * 78)
    print(f"跑 {len(tests)} 个测试")
    print("=" * 78)

    failures: list[tuple[str, str]] = []
    for test in tests:
        result = subprocess.run([PYTHON, str(test)], cwd=str(ROOT),
                                capture_output=True, text=True, errors="replace")
        mark = "OK  " if result.returncode == 0 else "FAIL"
        print(f"  [{mark}] {test.name}")
        if result.returncode != 0:
            output = (result.stdout or "") + (result.stderr or "")
            failures.append((test.name, output))

    print()
    print("=" * 78)
    if not failures:
        print(f"全部通过（{len(tests)} 个）")
        print("=" * 78)
        return 0

    print(f"失败 {len(failures)}/{len(tests)}")
    print("=" * 78)
    for name, output in failures:
        print()
        print("-" * 78)
        print(f"{name} 最后 {TAIL_LINES} 行")
        print("-" * 78)
        lines = output.strip().splitlines() or ["(没有任何输出)"]
        for line in lines[-TAIL_LINES:]:
            print(f"  {line}")
        log = ROOT / "tests" / (name.replace("test_", "").replace(".py", "") + ".log")
        if log.is_file():
            print(f"  (完整日志: {log.relative_to(ROOT)})")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
