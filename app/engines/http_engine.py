"""Bring-your-own-model over HTTP: any OpenAI-compatible chat endpoint.

This covers the practical ways someone plugs in "their own model" without writing a
plugin: OpenAI, DeepSeek, Moonshot, a self-hosted vLLM / one-api gateway, or a model
running locally under Ollama / LM Studio (both expose an OpenAI-compatible
``/v1/chat/completions``).

Translation is done with a prompt rather than a translation-specific API, so quality
depends on the model - but it is the only route that needs no release from anyone.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from ..config import TranslateSettings
from .base import EngineError, EngineSpec

USER_AGENT = "ruzh/1.0 (+openai-compatible engine)"

DEFAULT_PROMPT = (
    "你是字幕翻译。把下面的{src}文本翻译成{tgt}。"
    "只输出译文本身，不要解释、不要加引号、不要重复原文。"
)

#: Prompt values the user can put in the template.
LANGUAGE_NAMES = {
    "zh": "中文", "en": "英语", "ru": "俄语", "ja": "日语", "ko": "韩语",
    "fr": "法语", "de": "德语", "es": "西班牙语",
}


def endpoint_of(base_url: str) -> str:
    """Accept a base URL or a full endpoint and return a chat-completions URL.

    A bare host gets /v1 in front of the path. That is what every service documents as
    its base URL - https://api.deepseek.com, http://localhost:11434 - and gluing
    chat/completions onto it directly answers 404, which reads to the customer as "your
    software is broken" rather than "you missed four characters".

    A path the user chose is left alone: a gateway under /api/openai is not ours to
    rewrite, and if that one answers 404 the 测试连接 button now says so.
    """
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return ""
    if base.endswith("/chat/completions"):
        return base
    path = urllib.parse.urlsplit(base).path.rstrip("/")
    if not path:
        return f"{base}/v1/chat/completions"
    return f"{base}/chat/completions"


def explain_http_error(code: int, detail: str, url: str) -> str:
    """Say what an HTTP status means here, because the raw one is not actionable.

    401 and 404 are the two a customer will actually hit, and in both cases the status
    line alone leaves them guessing. Each answer names the thing to go and change.
    """
    if code == 401:
        return (f"HTTP 401 密钥不对（{detail or '接口拒绝了这把 key'}）。"
                f"检查设置里的 API Key；本地模型（Ollama / LM Studio）留空即可")
    if code == 403:
        return f"HTTP 403 没有权限（{detail or 'key 有效但这个模型不给用'}）"
    if code == 404:
        return (f"HTTP 404 接口地址不对（{url}）。"
                f"要填 /v1 那一层的地址，比如 https://api.deepseek.com/v1 或 "
                f"http://localhost:11434/v1")
    if code == 429:
        return f"HTTP 429 请求太频繁或者额度用完了（{detail}）"
    return f"HTTP {code} {detail}"


class OpenAIChatTranslator:
    """One HTTP engine instance, for one language pair."""

    is_pivot = False
    num_beams = 1

    def __init__(self, cfg, src: str, tgt: str,
                 settings: TranslateSettings | None = None) -> None:
        self.cfg = cfg
        self.src = src
        self.tgt = tgt
        self.settings = settings or TranslateSettings()
        self.base_url = getattr(cfg, "openai_base_url", "")
        self.api_key = getattr(cfg, "openai_api_key", "")
        self.model = getattr(cfg, "openai_model", "")
        self.prompt_template = getattr(cfg, "openai_prompt", "") or DEFAULT_PROMPT
        self.timeout = float(getattr(cfg, "openai_timeout", 60) or 60)
        self.label = f"联网 · 自定义大模型（{self.model or '未设置'}）  {src} → {tgt}"
        self.load_seconds = 0.0
        self.last_seconds = 0.0
        self._cache: OrderedDict[str, str] = OrderedDict()

    # -- interface ---------------------------------------------------------
    @property
    def ready(self) -> bool:
        return True

    def load(self) -> None:
        if not endpoint_of(self.base_url):
            raise EngineError("还没有填接口地址（高级页 → 自定义大模型）")
        if not self.model:
            raise EngineError("还没有填模型名（高级页 → 自定义大模型）")
        self.load_seconds = 0.0

    def set_beams(self, _requested) -> None:
        self.num_beams = 1

    def translate(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached

        started = time.perf_counter()
        out = self._request(text)
        self.last_seconds = time.perf_counter() - started

        self._cache[text] = out
        while len(self._cache) > max(16, int(self.settings.cache_size)):
            self._cache.popitem(last=False)
        return out

    def _prompt(self) -> str:
        try:
            return self.prompt_template.format(
                src=LANGUAGE_NAMES.get(self.src, self.src),
                tgt=LANGUAGE_NAMES.get(self.tgt, self.tgt),
                src_code=self.src, tgt_code=self.tgt,
            )
        except (KeyError, IndexError, ValueError):
            # A user template with a stray brace should not break translation.
            return DEFAULT_PROMPT.format(
                src=LANGUAGE_NAMES.get(self.src, self.src),
                tgt=LANGUAGE_NAMES.get(self.tgt, self.tgt))

    def _request(self, text: str) -> str:
        url = endpoint_of(self.base_url)
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._prompt()},
                {"role": "user", "content": text},
            ],
            "temperature": 0,
            "stream": False,
        }
        headers = {"Content-Type": "application/json", "User-Agent": USER_AGENT}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last: Exception | None = None
        for attempt in range(3):
            req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                         headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8", "replace"))
                return self._extract(data)
            except urllib.error.HTTPError as exc:
                detail = ""
                try:
                    detail = exc.read(400).decode("utf-8", "replace").strip()
                except Exception:  # noqa: BLE001
                    pass
                last = EngineError(explain_http_error(exc.code, detail, url))
                if exc.code not in (429, 500, 502, 503, 504):
                    break
            except urllib.error.URLError as exc:
                last = EngineError(f"网络错误：{exc.reason}")
            except EngineError as exc:
                last = exc
                break
            except Exception as exc:  # noqa: BLE001
                last = EngineError(f"{type(exc).__name__}: {exc}")
            time.sleep(0.7 * (attempt + 1))
        raise EngineError(f"调用自定义模型失败：{last}")

    @staticmethod
    def _extract(data: dict) -> str:
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise EngineError(f"接口返回的结构不认识：{str(data)[:200]}") from exc
        if isinstance(content, list):
            # Some gateways return content as a list of parts.
            content = "".join(part.get("text", "") for part in content
                              if isinstance(part, dict))
        return str(content).strip()

    def describe(self) -> str:
        return "\n".join([
            f"engine         : openai-compatible",
            f"endpoint       : {endpoint_of(self.base_url) or '(未设置)'}",
            f"model          : {self.model or '(未设置)'}",
            "note           : 识别到的文字会发送到这个接口",
        ])


def _availability(cfg, _models_root: str) -> tuple[bool, str]:
    if not endpoint_of(getattr(cfg, "openai_base_url", "")):
        return False, "还没填接口地址"
    if not getattr(cfg, "openai_model", ""):
        return False, "还没填模型名"
    return True, "调用你自己的模型"


def _factory(cfg, _models_root: str):
    from ..translator import TranslationRoute

    translator = OpenAIChatTranslator(cfg, cfg.source_lang, cfg.target_lang, cfg.translate)
    return TranslationRoute([translator], label=translator.label, is_pivot=False)


OPENAI_SPEC = EngineSpec(
    id="openai-compatible",
    label="联网 · 自定义大模型（OpenAI 兼容）",
    kind="http",
    hint="填一个 OpenAI 兼容的接口地址 + 模型名就能用：DeepSeek、Moonshot、"
         "本地 Ollama / LM Studio / vLLM、one-api 网关都行。",
    factory=_factory,
    availability=_availability,
)
