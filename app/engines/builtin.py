"""The engines that ship with the app.

Everything here is a thin description of code that already existed and is covered by
tests - the OPUS/Marian route, the NLLB route, and the three online providers. Wrapping
them in :class:`~app.engines.base.EngineSpec` is what makes them peers of a user's own
plugin instead of special cases in the UI.
"""

from __future__ import annotations

from .. import models
from ..translator import NllbTranslator, TranslationRoute, Translator
from .base import EngineSpec, EngineError


# -- offline: OPUS-MT -------------------------------------------------------
def _opus_supports(src: str, tgt: str) -> bool:
    return bool(models.resolve_route(src, tgt))


def _opus_availability(cfg, models_root: str) -> tuple[bool, str]:
    legs = models.resolve_route(cfg.source_lang, cfg.target_lang)
    if not legs:
        return False, "该语言对没有离线模型"
    if len(legs) > 1:
        missing = models.route_missing(models_root, cfg.source_lang, cfg.target_lang)
        if missing:
            return False, "；".join(missing)
        return True, "模型已就绪（经英语中转）"
    missing = models.missing_files(models_root, cfg.source_lang, cfg.target_lang)
    if missing:
        return False, f"缺 {len(missing)} 个模型文件"
    return True, "模型已就绪"


def _opus_factory(cfg, models_root: str) -> TranslationRoute:
    src, tgt = cfg.source_lang, cfg.target_lang
    legs = models.resolve_route(src, tgt)
    if not legs:
        raise EngineError(
            f"没有 {models.LANGUAGE_NAMES.get(src, src)} → "
            f"{models.LANGUAGE_NAMES.get(tgt, tgt)} 的离线模型。")
    translators = [
        Translator(models.cache_dir(models_root, leg_src, leg_tgt), cfg.translate)
        for leg_src, leg_tgt in legs
    ]
    return TranslationRoute(translators, label=models.route_label(src, tgt),
                            is_pivot=len(legs) > 1)


# -- offline: NLLB ----------------------------------------------------------
def _nllb_key() -> str:
    return "nllb-200-distilled-600M"


def _nllb_supports(src: str, tgt: str) -> bool:
    return bool(models.nllb_language(src)) and bool(models.nllb_language(tgt))


def _nllb_availability(cfg, models_root: str) -> tuple[bool, str]:
    key = _nllb_key()
    spec = models.multilingual_spec(key)
    if not _nllb_supports(cfg.source_lang, cfg.target_lang):
        return False, "NLLB 不支持该语言"
    missing = models.multilingual_missing(models_root, key)
    if missing:
        return False, f"缺 {len(missing)} 个 NLLB 文件（约 {spec['approx_mb']} MB）"
    return True, "NLLB 模型已就绪"


def _nllb_factory(cfg, models_root: str) -> TranslationRoute:
    key = _nllb_key()
    spec = models.multilingual_spec(key)
    src_lang = models.nllb_language(cfg.source_lang)
    tgt_lang = models.nllb_language(cfg.target_lang)
    translator = NllbTranslator(models.multilingual_dir(models_root, key),
                               src_lang, tgt_lang, cfg.translate)
    return TranslationRoute([translator],
                            label=f"{spec['label']}  {src_lang} → {tgt_lang}")


# -- online providers -------------------------------------------------------
def _provider_spec(provider_key: str, engine_id: str, label: str, kind_hint: str) -> EngineSpec:
    from .. import online

    def supports(src: str, tgt: str, _key=provider_key) -> bool:
        return online.build_provider(_key).supports(src, tgt)

    def availability(cfg, _root: str, _key=provider_key) -> tuple[bool, str]:
        provider = online.build_provider(_key, cfg)
        if not provider.supports(cfg.source_lang, cfg.target_lang):
            return False, (f"{models.LANGUAGE_NAMES.get(cfg.source_lang, cfg.source_lang)} → "
                           f"{models.LANGUAGE_NAMES.get(cfg.target_lang, cfg.target_lang)}"
                           f" 该接口不支持")
        problem = provider.config_problem(cfg)
        if problem:
            return False, problem
        return True, "联网翻译，不需要模型文件"

    def factory(cfg, _root: str, _key=provider_key) -> TranslationRoute:
        translator = online.OnlineTranslator(
            _key, cfg.source_lang.strip().lower(), cfg.target_lang.strip().lower(),
            cfg.translate, cfg)
        return TranslationRoute([translator], label=translator.label, is_pivot=False)

    return EngineSpec(id=engine_id, label=label, kind="online", hint=kind_hint,
                      origin="builtin", factory=factory, supports=supports,
                      availability=availability)


OPUS_SPEC = EngineSpec(
    id="opus",
    label="离线 · OPUS-MT（快）",
    kind="local",
    hint="完全离线，屏幕内容不出本机。小、快，每句约 0.4–0.8 秒；"
         "俄语↔中文需经英语中转，质量一般。",
    factory=_opus_factory,
    supports=_opus_supports,
    availability=_opus_availability,
)

NLLB_SPEC = EngineSpec(
    id="nllb",
    label="离线 · NLLB-200（已卸载）",
    kind="local",
    hint="实测：额外占用约 2 GB 内存，每句 10–19 秒 —— 不适合实时字幕，"
         "模型文件已删除。需要时用 --nllb 重新下载。",
    factory=_nllb_factory,
    supports=_nllb_supports,
    availability=_nllb_availability,
)

MYMEMORY_SPEC = _provider_spec(
    "mymemory", "mymemory", "联网 · MyMemory（免费）",
    "免费、无需密钥，中/英/俄 六向全通。缺点：匿名每日只有几千字符额度，"
    "而且识别到的文字会发到第三方服务器。")

YOUDAO_SPEC = _provider_spec(
    "youdao", "youdao", "联网 · 有道（免费，快）",
    "免费、无需密钥，英↔中通常 0.2–0.5 秒一句。但只稳定支持 英↔中，"
    "而且是非官方演示接口、限流很快。")

BAIDU_SPEC = _provider_spec(
    "baidu", "baidu", "联网 · 百度翻译（需密钥）",
    "官方接口，最稳、额度最大（标准版每月免费 5 万字符）。"
    "需要到 fanyi-api.baidu.com 申请 appid 和密钥，填在「词库/引擎」页。")


def builtin_specs() -> list[EngineSpec]:
    """Order matters: this is the order the dropdown shows."""
    return [MYMEMORY_SPEC, YOUDAO_SPEC, BAIDU_SPEC, OPUS_SPEC, NLLB_SPEC]
