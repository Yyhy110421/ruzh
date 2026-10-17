"""Online translation engines.

The offline engines keep everything on the machine. These do the opposite: whatever
is recognised on screen is sent to a third party over the network. That trade is
worth stating plainly, because the offline engines were chosen precisely to avoid it.

Reachability measured from this machine (a network where huggingface.co and
github.com are unreachable, so Google/LibreTranslate/Bing are all dead ends):

* **MyMemory** - keyless, real translations, direct `ru<->zh`. Anonymous quota is a
  few thousand characters per day, so it suits occasional use, not a whole film.
* **Youdao demo** - keyless and fast, but it is an unversioned demo host that may
  disappear; `zh->ru` already answers HTTP 411.
* **Baidu** - the official API, needs an appid/key, has a free monthly tier. This is
  the one to use for real subtitle work.

Every provider is normalised behind :class:`OnlineTranslator`, which gives the app the
same load/translate/set_beams surface the offline routes have.
"""

from __future__ import annotations

import hashlib
import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict

from .config import TranslateSettings

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

#: Smallest gap between two requests, to stay polite with keyless endpoints.
MIN_INTERVAL = 0.12


class OnlineError(RuntimeError):
    """``retryable`` marks failures that another attempt could plausibly fix."""

    def __init__(self, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _post_json(url: str, data, headers: dict | None = None, timeout: float = 20.0):
    hdrs = {"User-Agent": USER_AGENT}
    if headers:
        hdrs.update(headers)
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    elif isinstance(data, str):
        body = data.encode()
    else:
        body = data
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _get_json(url: str, timeout: float = 20.0):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# -- providers --------------------------------------------------------------
class Provider:
    """One online translation service."""

    key = ""
    label = ""
    #: Short language code -> the code this service expects.
    codes: dict[str, str] = {}
    #: Pairs the service actually handles. ``None`` means "any pair of known codes".
    #: Measured, not assumed: Youdao answers 102 (unsupported language type) for
    #: en<->ru, so advertising every combination would fail at translation time.
    pairs: set[tuple[str, str]] | None = None
    needs_credentials = False

    def supports(self, src: str, tgt: str) -> bool:
        if src not in self.codes or tgt not in self.codes:
            return False
        if self.pairs is None:
            return True
        return (src, tgt) in self.pairs

    def translate(self, text: str, src: str, tgt: str) -> str:  # pragma: no cover
        raise NotImplementedError

    def config_problem(self, cfg) -> str:
        """Return a human readable reason this provider cannot run, or ''."""
        return ""


class MyMemoryProvider(Provider):
    key = "mymemory"
    label = "联网 · MyMemory（免费，有每日额度）"
    codes = {"en": "en", "zh": "zh-CN", "ru": "ru", "ja": "ja", "ko": "ko",
             "fr": "fr", "de": "de", "es": "es"}

    def translate(self, text: str, src: str, tgt: str) -> str:
        pair = f"{self.codes[src]}|{self.codes[tgt]}"
        url = ("https://api.mymemory.translated.net/get?q="
               + urllib.parse.quote(text) + "&langpair=" + urllib.parse.quote(pair))
        data = _get_json(url)
        details = str(data.get("responseDetails") or "")
        if data.get("quotaFinished"):
            raise OnlineError("MyMemory 今日免费额度已用完，请换用其它引擎或明天再试")
        if "LIMIT" in details.upper():
            raise OnlineError(f"MyMemory 拒绝请求：{details}")
        text_out = (data.get("responseData") or {}).get("translatedText") or ""
        if not text_out or str(data.get("responseStatus")) != "200":
            raise OnlineError(f"MyMemory 返回异常：{details or data.get('responseStatus')}")
        return str(text_out)

class YoudaoDemoProvider(Provider):
    key = "youdao"
    label = "联网 · 有道（免费）"
    codes = {"en": "en", "zh": "zh-CHS", "ru": "ru"}
    #: Only English<->Chinese is dependable. en<->ru is refused outright (102), and
    #: ru<->zh intermittently returns 411 (rate limited) - it has worked, but not
    #: often enough to offer it as a supported pair.
    pairs = {("en", "zh"), ("zh", "en")}

    def translate(self, text: str, src: str, tgt: str) -> str:
        data = _post_json("https://aidemo.youdao.com/trans",
                          {"q": text, "from": self.codes[src], "to": self.codes[tgt]})
        code = str(data.get("errorCode", ""))
        if code == "411":
            # "访问频率受限" - throttling, not an unsupported pair.
            raise OnlineError("有道返回 411（访问频率受限），稍后重试", retryable=True)
        if code == "102":
            raise OnlineError("有道不支持这个语言对（errorCode=102）")
        if code not in ("0", ""):
            raise OnlineError(f"有道接口返回错误 errorCode={code}")
        translated = data.get("translation") or []
        if not translated:
            raise OnlineError("有道接口没有返回译文", retryable=True)
        return str(translated[0])


class BaiduProvider(Provider):
    key = "baidu"
    label = "联网 · 百度翻译（需 appid + 密钥）"
    codes = {"en": "en", "zh": "zh", "ru": "ru", "ja": "jp", "ko": "kor",
             "fr": "fra", "de": "de", "es": "spa"}
    needs_credentials = True

    def __init__(self, appid: str = "", key: str = "") -> None:
        self.appid = appid
        self.key = key

    def config_problem(self, cfg) -> str:
        if not (self.appid and self.key):
            return "使用百度翻译需要先填 appid 和密钥（「引擎与词库」标签页）"
        return ""

    def translate(self, text: str, src: str, tgt: str) -> str:
        salt = str(random.randint(10000, 999999))
        sign = hashlib.md5(
            (self.appid + text + salt + self.key).encode("utf-8")).hexdigest()
        data = _post_json("https://fanyi-api.baidu.com/api/trans/vip/translate", {
            "q": text, "from": self.codes[src], "to": self.codes[tgt],
            "appid": self.appid, "salt": salt, "sign": sign,
        })
        if data.get("error_code"):
            raise OnlineError(f"百度翻译返回错误 {data['error_code']}："
                              f"{data.get('error_msg', '')}")
        result = data.get("trans_result") or []
        if not result:
            raise OnlineError("百度翻译没有返回译文")
        return str(result[0].get("dst", ""))


def build_provider(key: str, cfg=None) -> Provider:
    """Providers are created per translator: nothing is shared, so no stale keys."""
    if key == "mymemory":
        return MyMemoryProvider()
    if key == "youdao":
        return YoudaoDemoProvider()
    if key == "baidu":
        return BaiduProvider(
            appid=getattr(cfg, "baidu_appid", "") if cfg else "",
            key=getattr(cfg, "baidu_key", "") if cfg else "",
        )
    raise OnlineError(f"未知的联网引擎：{key}")


PROVIDER_KEYS = ("mymemory", "youdao", "baidu")


# -- translator wrapper -----------------------------------------------------
class OnlineTranslator:
    """Gives an online provider the same surface as the offline translators."""

    def __init__(self, provider_key: str, src: str, tgt: str,
                 settings: TranslateSettings | None = None, cfg=None) -> None:
        self.provider = build_provider(provider_key, cfg)
        self.src = src
        self.tgt = tgt
        self.settings = settings or TranslateSettings()
        self.cfg = cfg
        self._cache: OrderedDict[str, str] = OrderedDict()
        self._last_request = 0.0
        self.load_seconds = 0.0
        self.last_seconds = 0.0
        self.num_beams = 1
        self.label = f"{self.provider.label}  {src} → {tgt}"

    # -- interface parity --------------------------------------------------
    @property
    def ready(self) -> bool:
        return True

    @property
    def is_pivot(self) -> bool:
        return False

    def set_beams(self, _requested) -> None:
        """Decoding effort is the provider's business, not ours."""
        self.num_beams = 1

    def load(self) -> None:
        problem = self.provider.config_problem(self.cfg) if self.cfg is not None else ""
        if problem:
            raise OnlineError(problem)
        if not self.provider.supports(self.src, self.tgt):
            raise OnlineError(f"{self.provider.label} 不支持 {self.src} → {self.tgt}")
        # Deliberately no probe request: on the keyless services every call spends
        # quota, and a bad key shows up on the first real translation anyway.
        self.load_seconds = 0.0

    def translate(self, text: str) -> str:
        text = (text or "").strip()
        if not text:
            return ""
        cached = self._cache.get(text)
        if cached is not None:
            self._cache.move_to_end(text)
            return cached

        started = time.perf_counter()
        result = self._translate_with_retry(text)
        self.last_seconds = time.perf_counter() - started

        self._cache[text] = result
        while len(self._cache) > max(16, int(self.settings.cache_size)):
            self._cache.popitem(last=False)
        return result

    def _translate_with_retry(self, text: str) -> str:
        last: Exception | None = None
        for attempt in range(3):
            gap = time.perf_counter() - self._last_request
            if gap < MIN_INTERVAL:
                time.sleep(MIN_INTERVAL - gap)
            self._last_request = time.perf_counter()
            try:
                return self.provider.translate(text, self.src, self.tgt)
            except urllib.error.HTTPError as exc:
                # 429/5xx are worth another try; other 4xx generally are not.
                last = OnlineError(f"HTTP {exc.code}", retryable=exc.code in
                                   (429, 500, 502, 503, 504))
            except urllib.error.URLError as exc:
                last = OnlineError(f"网络错误：{exc.reason}", retryable=True)
            except OnlineError as exc:
                last = exc
            except Exception as exc:  # noqa: BLE001
                last = OnlineError(f"{type(exc).__name__}: {exc}")
            if not getattr(last, "retryable", False):
                break
            time.sleep(0.6 * (attempt + 1))
        raise OnlineError(f"联网翻译失败：{last}")

    def describe(self) -> str:
        return "\n".join([
            f"engine         : online ({self.provider.key})",
            f"pair           : {self.src} -> {self.tgt}",
            "note           : 识别到的文字会发送到第三方服务器",
        ])
