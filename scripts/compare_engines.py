"""Compare the pivot route against a direct model on real trade sentences.

The offline default is ru -> en -> zh: two Helsinki models chained, with the loss of one
hop on each side. NLLB covers rus_Cyrl -> zho_Hans directly. This prints both for the same
sentences so the difference is visible instead of argued about.

    python scripts/compare_engines.py
"""
import io
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import engine as engine_mod  # noqa: E402
from app.config import AppConfig  # noqa: E402

SENTENCES = [
    "Общая сумма договора 600 000 рублей с учётом НДС.",
    "Предоплата 30 процентов в течение 5 банковских дней.",
    "Срок поставки 45 календарных дней с момента оплаты.",
    "Поставщик обязуется поставить товар надлежащего качества.",
    "В случае просрочки поставки поставщик уплачивает неустойку.",
    "Стоимость доставки до склада покупателя включена в цену.",
    "Оплата производится в рублях по курсу Центрального банка.",
    "Гарантийный срок составляет 12 месяцев с даты поставки.",
    "Товар должен соответствовать требованиям технического регламента.",
    "Настоящий договор вступает в силу с момента подписания.",
]


def run(engine_id: str) -> list[str]:
    cfg = AppConfig()
    cfg.source_lang, cfg.target_lang = "ru", "zh"
    cfg.translate.engine = engine_id
    root = cfg.resolved_models_root()
    ready, detail = engine_mod.check(cfg, root)
    if not ready:
        print(f"  {engine_id}: 不可用 —— {detail}")
        return []
    route = engine_mod.build(cfg, root)
    started = time.perf_counter()
    route.load()
    print(f"  {engine_id}: {route.label}  加载 {time.perf_counter()-started:.1f}s")
    out = []
    total = 0.0
    for text in SENTENCES:
        t0 = time.perf_counter()
        out.append(route.translate(text))
        total += time.perf_counter() - t0
    print(f"  {engine_id}: 总 {total:.1f}s，平均 {total*1000/len(SENTENCES):.0f} ms/句")
    return out


def main() -> int:
    print("=" * 78)
    print("离线引擎对比：中转 (opus ru→en→zh) vs 直译 (nllb ru→zh)")
    print("=" * 78)
    pivot = run("opus")
    print()
    direct = run("nllb")
    if not direct:
        print()
        print("直译引擎不可用，先下载：python scripts/setup_models.py --nllb")
        return 1

    print()
    print("=" * 78)
    for index, source in enumerate(SENTENCES):
        print(f"[{index+1}] {source}")
        print(f"  中转: {pivot[index] if pivot else '(无)'}")
        print(f"  直译: {direct[index]}")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
