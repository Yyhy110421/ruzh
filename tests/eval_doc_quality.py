"""Does rebuilding the paragraph actually improve the translation?

The whole point of app/segment.py is that a half sentence translates badly. This runs the
same hard-wrapped trade text through both paths with the real offline engine and prints the
results next to each other, so the claim can be checked rather than assumed.

It loads the model twice and takes a couple of minutes, which is why it is an evaluation
script and not part of the test suite.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_LOG = open(ROOT / "tests" / "doc_quality.log", "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app.config import AppConfig  # noqa: E402
from app.segment import rebuild_paragraphs, split_sentences  # noqa: E402
from app.text_tool import translate_text  # noqa: E402

#: Exactly how it arrives when copied out of an email client or a PDF: hard-wrapped at a
#: column that has nothing to do with where the sentences end.
HARD_WRAPPED = """Здравствуйте!

Мы получили ваш запрос на поставку спецодежды для склада.
Срок поставки — 30 календарных дней с момента
поступления оплаты на наш расчётный счёт.
Цена указана за одну тонну без учёта доставки.
Подтвердите, пожалуйста, количество и размеры."""


def make_config() -> AppConfig:
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = "opus"
    return cfg


def main() -> int:
    print("=" * 78)
    print("输入（硬折行，就是它到手的样子）")
    print("=" * 78)
    for line in HARD_WRAPPED.split("\n"):
        print(f"  | {line}")
    print()

    print("=" * 78)
    print("段落模式实际送进引擎的单位")
    print("=" * 78)
    for index, block in enumerate(rebuild_paragraphs(HARD_WRAPPED), start=1):
        if block.kind == "blank":
            continue
        sentences = split_sentences(block.text)
        print(f"  第 {index} 段：{len(sentences)} 句")
        for sentence in sentences:
            print(f"      . {sentence}")
    print()

    results = {}
    for mode in ("line", "paragraph"):
        cfg = make_config()
        print(f"跑 {mode} 模式…")
        started = time.perf_counter()
        try:
            result = translate_text(cfg, cfg.resolved_models_root(), HARD_WRAPPED,
                                    mode=mode)
        except Exception as exc:  # noqa: BLE001
            print(f"  失败：{exc}")
            return 1
        results[mode] = result
        print(f"  完成，{result.sentences} 个单位，用时 {time.perf_counter() - started:.1f} 秒")
    print()

    for mode in ("line", "paragraph"):
        print("=" * 78)
        print(f"{mode} 模式的译文")
        print("=" * 78)
        for line in results[mode].translation.split("\n"):
            print(f"  > {line}")
        print()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)
