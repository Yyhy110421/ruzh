"""The translation engine contract.

Every backend - the bundled offline models, the built-in online services, a local
script the user dropped in, a remote HTTP endpoint - is described by one
:class:`EngineSpec` and produces one object with the same tiny surface:

============================  ===========================================
``load()``                    optional; prepare whatever is needed
``translate(text) -> str``    required
``set_beams(n)``              optional; ignore it if the concept does not apply
============================  ===========================================

Nothing else is required, which is the point: the app does not know or care whether
the text is produced by a 77M Marian model, a 600M multilingual model, someone's own
fine-tune behind an HTTP API, or a function the user wrote this morning.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from ..translator import TranslateError


class EngineError(TranslateError):
    """Raised for anything an engine cannot do: missing files, bad config, network."""


@dataclass
class EngineSpec:
    """Everything the UI and the factory need to know about one engine."""

    id: str
    label: str
    #: "local" | "online" | "custom" | "http" - used for wording and warnings.
    kind: str
    #: One line shown under the dropdown.
    hint: str = ""
    #: Where it came from: "builtin" or the path of the file that defined it.
    origin: str = "builtin"
    #: Build the translator. Raises EngineError with a usable message.
    factory: Callable[[Any, str], Any] | None = None
    #: Can this engine serve the pair? Defaults to checking ``languages``.
    supports: Callable[[str, str], bool] | None = None
    #: (ready, message) - whether it can run right now, and what to tell the user.
    availability: Callable[[Any, str], tuple[bool, str]] | None = None
    #: ISO language codes this engine handles; empty means "ask supports()".
    languages: list[str] = field(default_factory=list)

    def can_handle(self, src: str, tgt: str) -> bool:
        if self.supports is not None:
            return bool(self.supports(src, tgt))
        if self.languages:
            return src in self.languages and tgt in self.languages
        return True

    def check(self, cfg, models_root: str) -> tuple[bool, str]:
        if self.availability is not None:
            return self.availability(cfg, models_root)
        return True, ""

    def build(self, cfg, models_root: str):
        if self.factory is None:
            raise EngineError(f"引擎 {self.id} 没有提供构建函数")
        return self.factory(cfg, models_root)


class CustomEngineAdapter:
    """Wraps an object supplied by user code so the pipeline can drive it.

    Only ``translate`` is mandatory. Anything missing gets a safe default, and every
    call is guarded so a broken plugin degrades into an error message instead of
    taking the whole app down mid-subtitle.
    """

    is_pivot = False

    def __init__(self, target, spec: EngineSpec, src: str, tgt: str) -> None:
        self._target = target
        self.spec = spec
        self.src = src
        self.tgt = tgt
        self.label = getattr(target, "label", spec.label)
        self.num_beams = 1
        self.load_seconds = 0.0
        self.last_seconds = 0.0
        self._loaded = False

    @property
    def ready(self) -> bool:
        return getattr(self._target, "ready", True)

    def load(self) -> None:
        if self._loaded:
            return
        loader = getattr(self._target, "load", None)
        if callable(loader):
            try:
                loader()
            except Exception as exc:  # noqa: BLE001
                raise EngineError(f"引擎 {self.spec.id} 初始化失败：{exc}") from exc
        self._loaded = True

    def set_beams(self, requested) -> None:
        setter = getattr(self._target, "set_beams", None)
        if callable(setter):
            try:
                setter(requested)
            except Exception:  # noqa: BLE001
                pass
        try:
            self.num_beams = int(getattr(self._target, "num_beams", 1) or 1)
        except (TypeError, ValueError):
            self.num_beams = 1

    def translate(self, text: str) -> str:
        translate = getattr(self._target, "translate", None)
        if not callable(translate):
            raise EngineError(f"引擎 {self.spec.id} 没有提供 translate() 方法")
        try:
            result = translate(text)
        except EngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise EngineError(f"引擎 {self.spec.id} 出错：{exc}") from exc
        if result is None:
            return ""
        if not isinstance(result, str):
            raise EngineError(f"引擎 {self.spec.id} 的 translate() 必须返回字符串，"
                              f"实际返回 {type(result).__name__}")
        return result

    def describe(self) -> str:
        origin = self.spec.origin
        extra = getattr(self._target, "describe", None)
        body = extra() if callable(extra) else ""
        return "\n".join(filter(None, [
            f"engine         : custom ({self.spec.id})",
            f"source file    : {origin}",
            f"pair           : {self.src} -> {self.tgt}",
            body,
        ]))
