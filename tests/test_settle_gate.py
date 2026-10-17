"""The commit gate must open on a live screen, not only on a frozen one.

The reported failure: the program sat there recognising frame after frame, 翻译 0 ms,
译文 empty, forever. The saved config had settle_frames = 5, and the gate compared reads
for *byte* equality. Antialiased text never reads identically twice, so "five identical
frames" never happened and nothing was ever translated.

_should_commit does not set _last_committed itself - run() does, after translating - so a
direct caller has to do the same or every case looks like "same text, translate again".

Run: python tests/test_settle_gate.py
"""
import queue
import random
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "settle_gate.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.pipeline import Pipeline  # noqa: E402

failures = 0


def check(ok, label, extra=""):
    global failures
    if not ok:
        failures += 1
    print(f"  [{'OK  ' if ok else 'FAIL'}] {label}" + (f"  {extra}" if extra else ""))


def fresh(**kw):
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "en", "zh"
    for k, v in kw.items():
        setattr(cfg, k, v)
    return Pipeline(cfg, queue.Queue()), cfg


BASE = ("Edit View Repository Branch Help Let's get started! Add a repository to "
        "GitHub Desktop to start collaborating Filter your repositories")


def noisy(text, rng, edits=2):
    """A live window: a couple of characters come out differently each read."""
    chars = list(text)
    for _ in range(edits):
        i = rng.randrange(len(chars))
        chars[i] = rng.choice("aeioulnrst")
    return "".join(chars)


def commit(pl, cfg, text):
    """What run() does around the gate: set _last_committed once it fires."""
    fired = pl._should_commit(text, cfg)
    if fired:
        pl._last_committed = text
        pl._pending = text
        pl._pending_count = 0
        pl._pending_since = 0.0
    return fired


def main() -> int:
    print("=" * 78)
    print("一、用户那组配置：settle_frames=5 + 每帧有几个错字的识别结果")
    print("=" * 78)
    pl, cfg = fresh(settle_frames=5, jitter_tolerance=0.08)
    cfg.defer_while_growing = False
    pl.settle_budget = 30.0                # 只测帧数这条规则，不让保险兜底
    rng = random.Random(7)
    fired = None
    for i in range(20):
        if commit(pl, cfg, noisy(BASE, rng)):
            fired = i + 1
            break
    check(fired is not None, "最终提交了", f"第 {fired} 帧" if fired else "20 帧都没提交")
    check(fired is not None and fired <= 7, "5 帧左右提交（旧代码永远不提交）", f"第 {fired} 帧")
    print()

    print("=" * 78)
    print("二、完全一模一样的连续读取，仍按 settle_frames 走")
    print("=" * 78)
    pl2, cfg2 = fresh(settle_frames=3)
    cfg2.defer_while_growing = False
    pl2.settle_budget = 30.0
    seq = [commit(pl2, cfg2, BASE) for _ in range(3)]
    check(seq == [False, False, True], f"第 3 次才提交: {seq}")
    check(commit(pl2, cfg2, BASE) is False, "已经提交过的相同文本不再翻")
    print()

    print("=" * 78)
    print("三、每帧内容都不同时，时间保险必须兜住")
    print("=" * 78)
    pl3, cfg3 = fresh(settle_frames=99)
    cfg3.defer_while_growing = False
    pl3.settle_budget = 0.05
    commit(pl3, cfg3, "aaa bbb ccc ddd")
    time.sleep(0.08)
    check(commit(pl3, cfg3, "zzz yyy xxx www") is True,
          "settle_frames 要 99 次，但超时后照样翻译")
    print()

    print("=" * 78)
    print("四、逐字打出来的字幕还是先忍住（defer_while_growing 没被破坏）")
    print("=" * 78)
    pl4, cfg4 = fresh(settle_frames=1)
    cfg4.defer_while_growing = True
    cfg4.max_defer_frames = 8
    pl4.settle_budget = 30.0
    commit(pl4, cfg4, "Hello")
    grown = [commit(pl4, cfg4, "Hello wor"), commit(pl4, cfg4, "Hello world")]
    check(grown == [False, False], f"生长过程中先不翻: {grown}")
    pl4.settle_budget = 0.0
    check(commit(pl4, cfg4, "Hello world") is True, "保险到了才翻")
    print()

    print("=" * 78)
    print("五、和已提交文本一致时不重复翻")
    print("=" * 78)
    pl5, cfg5 = fresh(settle_frames=1)
    cfg5.defer_while_growing = False
    check(commit(pl5, cfg5, "Some line") is True, "第一次提交")
    check(commit(pl5, cfg5, "Some line") is False, "相同文本不再提交")
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
