"""Facade over the engine registry.

Historically this module held the engine table itself; it now just forwards to
:mod:`app.engines` so existing call sites (UI, pipeline, doctor) keep working while the
catalog gains user plugins and the bring-your-own-model HTTP engine.
"""

from __future__ import annotations

from . import engines, models
from .config import AppConfig
from .engines import EngineError, EngineSpec
from .translator import TranslationRoute

__all__ = [
    "EngineError", "EngineSpec", "build", "check", "missing_summary",
    "engine_choices", "catalog", "reload", "problems", "engine_kind",
    "engine_label", "is_online", "resolve_id", "default_engine", "models",
]


def catalog() -> dict[str, EngineSpec]:
    return engines.catalog()


def reload() -> dict[str, EngineSpec]:
    return engines.reload()


def problems() -> list:
    return engines.problems()


def engine_choices() -> dict[str, str]:
    """engine id -> label, in display order, for the UI dropdown."""
    return {key: spec.label for key, spec in engines.catalog().items()}


def engine_label(engine_id: str) -> str:
    spec = engines.get(engine_id)
    return spec.label if spec else engine_id


def engine_kind(engine_id: str) -> str:
    spec = engines.get(engine_id)
    return spec.kind if spec else "unknown"


def resolve_id(engine_id: str, models_root: str | None = None) -> str:
    """The engine id that will actually be used, after the default is applied."""
    return engines.resolve_id(engine_id, models_root)


def is_online(engine_id: str) -> bool:
    return engine_kind(engine_id) in ("online", "http")


def default_engine() -> str:
    return engines.default_engine()


def build(cfg: AppConfig, models_root: str) -> TranslationRoute:
    return engines.build(cfg, models_root)


def check(cfg: AppConfig, models_root: str) -> tuple[bool, str]:
    return engines.check(cfg, models_root)


def missing_summary(cfg: AppConfig, models_root: str) -> str:
    return engines.missing_summary(cfg, models_root)
