"""Test the online engines through the app's own engine layer."""
import os
import sys
import time
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

_LOG = open(os.path.join(ROOT, "tests", "online_engine.log"), "w", encoding="utf-8", errors="replace")
sys.stdout = _LOG
sys.stderr = _LOG

from app import bootstrap  # noqa: E402

bootstrap.prepare()

from app import engine as engine_mod  # noqa: E402
from app import models  # noqa: E402
from app.config import AppConfig  # noqa: E402

CASES = {
    ("en", "zh"): "We need to talk about what happened last night.",
    ("zh", "en"): "政府宣布了减少大城市空气污染的新措施。",
    ("ru", "zh"): "Встреча перенесена на три часа дня завтра.",
    ("zh", "ru"): "会议已移至明天下午三点。",
}


def run(engine_key: str, pairs, unsupported=()) -> int:
    """Returns the number of failures for this engine.

    ``unsupported`` lists pairs the engine correctly refuses up front; refusing is
    the right behaviour, not a failure.
    """
    failures = 0
    print("=" * 74)
    print(f"引擎 {engine_key}  ({engine_mod.engine_label(engine_key)})")
    print("=" * 74)
    root = os.path.join(ROOT, "models")
    online = engine_mod.engine_kind(engine_key) in ("online", "http")

    for src, tgt in pairs:
        cfg = AppConfig()
        cfg.source_lang, cfg.target_lang = src, tgt
        cfg.translate.engine = engine_key

        ready, detail = engine_mod.check(cfg, root)
        if not ready:
            if (src, tgt) in unsupported:
                print(f"  [OK  ] {src}->{tgt} 如预期不支持: {detail}")
            else:
                print(f"  [FAIL] {src}->{tgt} 不满足条件: {detail}")
                failures += 1
            continue
        if (src, tgt) in unsupported:
            print(f"  [FAIL] {src}->{tgt} 本该拒绝却报告可用")
            failures += 1
            continue

        try:
            route = engine_mod.build(cfg, root)
            route.load()
            route.set_beams(4)
            if online:
                # Decoding effort belongs to the provider; accepting a beam count
                # here would silently misrepresent what the engine does.
                if route.num_beams != 1:
                    raise AssertionError(f"联网引擎不该接受束搜索设置（得到 {route.num_beams}）")
                if route.is_pivot:
                    raise AssertionError("联网引擎不该标记为中转")

            text = CASES[(src, tgt)]
            t0 = time.perf_counter()
            out = route.translate(text)
            first = (time.perf_counter() - t0) * 1000
            t0 = time.perf_counter()
            again = route.translate(text)
            cached = (time.perf_counter() - t0) * 1000

            print(f"  [OK  ] {src}->{tgt}  {first:6.0f} ms（缓存 {cached:.1f} ms）")
            print(f"         {text}")
            print(f"      -> {out}")
            if not out.strip():
                print("         [FAIL] 译文为空")
                failures += 1
            if again != out:
                print("         [FAIL] 缓存不一致")
                failures += 1
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] {src}->{tgt}: {exc}")
            traceback.print_exc()
            failures += 1
    print()
    return failures


def main() -> int:
    failures = 0
    all_pairs = list(CASES)

    failures += run("mymemory", all_pairs)
    # Measured: Youdao answers 102 (unsupported language type) for en<->ru, and only
    # en<->zh is dependable, so the provider declares just that pair.
    failures += run("youdao", all_pairs, unsupported={("ru", "zh"), ("zh", "ru"), ("en", "ru")})

    print("=" * 74)
    print("百度（未配置密钥，应给出清晰提示）")
    print("=" * 74)
    cfg = AppConfig()
    cfg.translate.engine = "baidu"
    ready, detail = engine_mod.check(cfg, os.path.join(ROOT, "models"))
    print(f"  check -> ready={ready}  detail={detail}")
    if ready:
        failures += 1
        print("  [FAIL] 没配密钥却报告可用")
    else:
        print("  [OK  ] 正确报告缺少凭据")
    print()

    failures += run("opus", [("en", "zh"), ("ru", "zh")])

    print("=" * 74)
    print(f"失败 {failures} 项")
    return 1 if failures else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
