"""Engine registry: one place that knows every way this app can translate.

Four sources feed the list the UI shows:

* the engines that ship with the app (:mod:`~app.engines.builtin`)
* the OpenAI-compatible HTTP engine, configured with a URL and a model name
* plugins the user dropped into ``engines_custom/`` (:mod:`~app.engines.custom`)
* whatever those plugins failed to load, reported instead of hidden

Callers only ever need :func:`catalog`, :func:`build` and :func:`check`.
"""

from __future__ import annotations

from pathlib import Path

from ..translator import TranslationRoute
from . import builtin, custom, http_engine, local_llm
from .base import EngineError, EngineSpec

__all__ = [
    "EngineError", "EngineSpec", "catalog", "build", "check", "missing_summary",
    "problems", "reload", "get", "default_engine", "resolve_id", "engine_dir",
    "write_example", "plugins_dir",
]

#: Populated by :func:`catalog` and invalidated by :func:`reload`.
_cache: dict[str, EngineSpec] | None = None
_problems: list = []


def plugins_dir() -> Path:
    """The folder a user drops plugins into, created and documented on demand."""
    from ..config import APP_DIR

    return custom.write_example(APP_DIR)


def engine_dir() -> Path:
    return plugins_dir()


def write_example() -> Path:
    from ..config import APP_DIR

    return custom.write_example(APP_DIR)


def _build_all() -> tuple[dict[str, EngineSpec], list]:
    specs: list[EngineSpec] = [http_engine.OPENAI_SPEC, local_llm.LOCAL_LLM_SPEC]
    specs += builtin.builtin_specs()
    problems: list = []

    from ..config import APP_DIR

    found, failures = custom.discover(APP_DIR)
    problems.extend(failures)
    specs.extend(found)

    by_id: dict[str, EngineSpec] = {}
    for spec in specs:
        if spec.id in by_id:
            problems.append(custom.LoadProblem(
                spec.origin, f"引擎 id 「{spec.id}」重复，后一个被忽略"))
            continue
        by_id[spec.id] = spec
    return by_id, problems


def catalog() -> dict[str, EngineSpec]:
    """id -> spec, in display order. Cached until :func:`reload`."""
    global _cache, _problems
    if _cache is None:
        _cache, _problems = _build_all()
    return _cache


def reload() -> dict[str, EngineSpec]:
    """Re-scan the plugin folder (so a new engine needs no restart)."""
    global _cache
    _cache = None
    return catalog()


def problems() -> list:
    return list(_problems)


def get(engine_id: str) -> EngineSpec | None:
    return catalog().get(engine_id)


def _offline_ready(models_root: str | None = None) -> bool:
    """Is any offline model actually installed on this machine?"""
    try:
        from .. import models
        from ..config import MODELS_DIR

        root = models_root or str(MODELS_DIR)
        for (src, tgt) in models.MODEL_REGISTRY:
            if not models.missing_files(root, src, tgt):
                return True
    except Exception:  # noqa: BLE001
        pass
    return False


def default_engine(models_root: str | None = None) -> str:
    """Pick the engine a fresh install should start on.

    Offline when the models are present, because it has no daily quota to run into and
    the recognised text never leaves the machine - the two properties that make it the
    right default both for a normal user and for someone translating business material.
    A source checkout without models gets an online engine instead, so the app is
    useful before anything has been downloaded.
    """
    available = catalog()
    if "opus" in available and _offline_ready(models_root):
        return "opus"
    for candidate in ("mymemory", "youdao", "opus"):
        if candidate in available:
            return candidate
    return next(iter(available), "opus")


def resolve_id(engine_id: str, models_root: str | None = None) -> str:
    """An explicit engine id, or the default when it is blank or no longer exists."""
    available = catalog()
    if engine_id and engine_id in available:
        return engine_id
    return default_engine(models_root)


def check(cfg, models_root: str) -> tuple[bool, str]:
    engine_id = resolve_id(cfg.translate.engine, models_root)
    spec = get(engine_id)
    if spec is None:
        return False, f"没有名为「{cfg.translate.engine}」的引擎，请重新选择"
    src = cfg.source_lang.strip().lower()
    tgt = cfg.target_lang.strip().lower()
    if not spec.can_handle(src, tgt):
        return False, "该引擎不支持这个语言对"
    # Write the resolved choice back so the UI and the pipeline agree on what runs.
    cfg.translate.engine = engine_id
    return spec.check(cfg, models_root)


def build(cfg, models_root: str) -> TranslationRoute:
    engine_id = resolve_id(cfg.translate.engine, models_root)
    spec = get(engine_id)
    if spec is None:
        raise EngineError(f"没有名为「{cfg.translate.engine}」的引擎")
    cfg.translate.engine = engine_id
    return spec.build(cfg, models_root)


def missing_summary(cfg, models_root: str) -> str:
    spec = get(resolve_id(cfg.translate.engine, models_root))
    if spec is None:
        return f"没有名为「{cfg.translate.engine}」的引擎。"
    ready, detail = spec.check(cfg, models_root)
    if ready:
        return ""
    if spec.kind == "local":
        return f"{detail}。请在主界面点击「下载模型」。"
    return detail
