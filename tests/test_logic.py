"""Dev test for the two new pieces of logic: CJK punctuation and subtitle deferral."""
import os
import queue
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_LOG = open(os.path.join(ROOT, "tests", "logic_out.log"), "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402
from app.translator import normalize_cjk_punctuation, tidy_cjk  # noqa: E402

PUNCT_CASES = [
    ("你要明白,我从来没有故意要发生这一切.", "你要明白，我从来没有故意要发生这一切。"),
    ("请记住在关闭应用程序前保存您的工作 。", "请记住在关闭应用程序前保存您的工作。"),
    ("销售额增加了 12.5% 。", "销售额增加了 12.5% 。"),
    ("等等... 我还没说完.", "等等... 我还没说完。"),
    ("This is fine, really.", "This is fine, really."),
    ("他说: 我不去; 你走吧!", "他说： 我不去； 你走吧！"),
    ("", ""),
]


def check_punctuation() -> int:
    print("=" * 66)
    print("标点规范化")
    print("=" * 66)
    failures = 0
    for raw, expected in PUNCT_CASES:
        got = normalize_cjk_punctuation(tidy_cjk(raw))
        ok = got == expected
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} {raw!r}\n     -> {got!r}")
        if not ok:
            print(f"     期望 {expected!r}")
    return failures


def drive(cfg: AppConfig, reads: list[str]) -> list[int]:
    """Feed a sequence of OCR reads through _should_commit; return commit indices."""
    pipeline = Pipeline(cfg, queue.Queue(maxsize=64))
    commits = []
    for index, text in enumerate(reads):
        if pipeline._should_commit(text, cfg):
            pipeline._last_committed = text
            pipeline._pending = text
            pipeline._pending_count = 0
            commits.append(index)
    return commits


def check_deferral() -> int:
    print()
    print("=" * 66)
    print("字幕逐字浮现 / 抖动 / 换行")
    print("=" * 66)
    failures = 0

    def show(name: str, reads: list[str], expect: list[int], cfg: AppConfig) -> None:
        nonlocal failures
        got = drive(cfg, reads)
        ok = got == expect
        if not ok:
            failures += 1
        print(f"{'OK  ' if ok else 'FAIL'} {name}")
        print(f"     读到 {len(reads)} 帧，翻译 {len(got)} 次，位置 {got}（期望 {expect}）")
        for i, r in enumerate(reads):
            print(f"       [{i}] {'翻' if i in got else '  '} {r!r}")

    base = AppConfig()

    # A subtitle typing itself out, then holding steady. Only the first fragment and
    # the finished line should be translated.
    typed = ["The", "The quick", "The quick brown", "The quick brown fox",
             "The quick brown fox jumps", "The quick brown fox jumps over",
             "The quick brown fox jumps over the lazy dog.",
             "The quick brown fox jumps over the lazy dog."]
    show("逐字浮现后稳定", typed, [0, 7], base)

    # OCR jitter on a settled line must not retranslate.
    show("识别抖动", ["Hello world.", "Hello world,", "Hello world.",
                    "Hello world."], [0], base)

    # A genuinely different line must be translated.
    show("换了一句话", ["Hello world.", "How are you today?"], [0, 1], base)

    # Truncation (subtitle shrinking) is also treated as still changing.
    show("文字变短", ["The quick brown fox", "The quick brown", "The quick brown"],
         [0, 2], base)

    # Safety valve: a line that keeps growing forever still gets shown.
    runaway = [("x" * n) for n in range(1, 20)]
    got = drive(base, runaway)
    print(f"{'OK  ' if got else 'FAIL'} 无限增长时的保险丝（max_defer_frames={base.max_defer_frames}）")
    print(f"     读到 {len(runaway)} 帧，翻译 {len(got)} 次，位置 {got}")
    if len(got) < 2:
        failures += 1
        print("     期望多次翻译：只翻一次说明增长被当成噪声吃掉了，屏幕会停在旧译文")

    # The same thing with realistic words: a line revealed one character at a time
    # must still end up translated.
    slow = ["H", "He", "Hel", "Hell", "Hello", "Hello ", "Hello w", "Hello wo",
            "Hello wor", "Hello worl", "Hello world"]
    slow_commits = drive(base, slow)
    print(f"{'OK  ' if len(slow_commits) >= 2 else 'FAIL'} 每帧只多一个字符也要最终翻译")
    print(f"     位置 {slow_commits}")
    if len(slow_commits) < 2:
        failures += 1

    # Disabling the feature must translate every intermediate state.
    off = AppConfig()
    off.defer_while_growing = False
    got_off = drive(off, typed)
    ok = got_off == list(range(len(typed) - 1))
    if not ok:
        failures += 1
    print(f"{'OK  ' if ok else 'FAIL'} 关闭该功能后每帧都翻译")
    print(f"     位置 {got_off}（期望 {list(range(len(typed) - 1))}）")

    return failures


def main() -> int:
    failures = check_punctuation()
    failures += check_deferral()
    print()
    print("=" * 66)
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
