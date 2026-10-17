"""Dev evaluation: translation quality on realistic subtitle-style sentences.

Compares greedy (1 beam) with the model's own recommended 4 beams, and reports
latency, so the quality/latency trade-off is a measurement rather than a guess.
"""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_LOG = open(os.path.join(ROOT, "tests", "quality_out.log"), "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import models  # noqa: E402
from app.config import TranslateSettings  # noqa: E402
from app.translator import Translator  # noqa: E402

# Movie / documentary / tech-talk style lines, i.e. what this tool actually sees.
SENTENCES = [
    "We need to talk about what happened last night.",
    "I've been waiting for this moment my whole life.",
    "The meeting has been moved to three o'clock tomorrow afternoon.",
    "You have to understand, I never meant for any of this to happen.",
    "This is the most important decision we will ever make together.",
    "Please make sure the file is saved before you shut down the computer.",
    "Nobody knows what is going to happen next, so we might as well enjoy it.",
    "According to the report, sales increased by twelve percent last quarter.",
    "If you do not mind, I would rather finish this conversation in private.",
    "The train leaves in ten minutes, so we should probably get going.",
    "He opened the door slowly and looked around the empty room.",
    "That is not what I asked for, but I suppose it will have to do.",
    "The government announced new measures to reduce air pollution in major cities.",
    "She smiled at him and said nothing, which made him even more nervous.",
    "Our research shows that most people are willing to pay more for better service.",
]


def run(beams: int) -> tuple[list[str], float]:
    root = os.path.join(ROOT, "models")
    settings = TranslateSettings(num_beams=beams)
    translator = Translator(models.cache_dir(root, "en", "zh"), settings)
    translator.load()
    print(f"beams={translator.num_beams}  length_penalty={translator.length_penalty}")
    outputs = []
    total = 0.0
    for text in SENTENCES:
        t0 = time.perf_counter()
        out = translator.translate(text)
        total += time.perf_counter() - t0
        outputs.append(out)
    return outputs, total


def main() -> int:
    root = os.path.join(ROOT, "models")
    if not models.is_installed(root, "en", "zh"):
        print("[FAIL] model missing")
        return 1

    results: dict[int, tuple[list[str], float]] = {}
    for beams in (1, 4):
        print("=" * 70)
        try:
            results[beams] = run(beams)
        except Exception:
            traceback.print_exc()
            return 1

    print("\n" + "=" * 70)
    print("原文 | 1 beam | 4 beams")
    print("=" * 70)
    for i, text in enumerate(SENTENCES):
        print(f"\n[{i + 1}] {text}")
        print(f"    1beam : {results[1][0][i]}")
        print(f"    4beams: {results[4][0][i]}")

    print("\n" + "=" * 70)
    n = len(SENTENCES)
    print(f"平均耗时  1 beam : {results[1][1] / n * 1000:.0f} ms/句")
    print(f"平均耗时  4 beams: {results[4][1] / n * 1000:.0f} ms/句")
    diff = sum(1 for i in range(n) if results[1][0][i] != results[4][0][i])
    print(f"两种解码结果不同的句子数: {diff}/{n}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
