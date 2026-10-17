"""Probe which keyless online translation endpoints actually work from this machine.

Each candidate is asked to translate the same en->zh sentence; we record the HTTP
status and the returned text. Run it before wiring any provider into the app: on a
network where huggingface.co and github.com are unreachable, several well known
"free" endpoints are unreachable too.
"""
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

TEXT = "The meeting has been moved to three o'clock tomorrow afternoon."
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
CTX = ssl.create_default_context()
TIMEOUT = 15


def fetch(url, data=None, headers=None, method=None):
    hdrs = {"User-Agent": UA, "Accept": "*/*"}
    if headers:
        hdrs.update(headers)
    body = None
    if data is not None:
        if isinstance(data, dict):
            body = urllib.parse.urlencode(data).encode()
            hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
        else:
            body = data
    req = urllib.request.Request(url, data=body, headers=hdrs, method=method)
    with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as resp:
        return resp.status, resp.read(4000).decode("utf-8", "replace")


def show(name, fn):
    try:
        status, body = fn()
        text = body.strip().replace("\n", " ")[:220]
        print(f"[ OK ] {name}\n        HTTP {status}  {text}\n")
        return True
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read(200).decode("utf-8", "replace").replace("\n", " ")
        except Exception:  # noqa: BLE001
            pass
        print(f"[FAIL] {name}\n        HTTP {exc.code}  {detail}\n")
    except Exception as exc:  # noqa: BLE001
        print(f"[FAIL] {name}\n        {type(exc).__name__}: {exc}\n")
    return False


def mymemory():
    q = urllib.parse.quote(TEXT)
    return fetch(f"https://api.mymemory.translated.net/get?q={q}&langpair=en|zh-CN")


def lingva():
    q = urllib.parse.quote(TEXT)
    return fetch(f"https://lingva.ml/api/v1/en/zh/{q}")


def youdao_demo():
    q = urllib.parse.quote(TEXT)
    return fetch(f"https://aidemo.youdao.com/trans?q={q}&from=en&to=zh-CHS")


def youdao_legacy():
    return fetch("https://fanyi.youdao.com/translate?&doctype=json&type=EN2ZH_CN&i="
                 + urllib.parse.quote(TEXT))


def google_direct():
    q = urllib.parse.quote(TEXT)
    return fetch("https://translate.googleapis.com/translate_a/single"
                 f"?client=gtx&sl=en&tl=zh-CN&dt=t&q={q}")


def bing():
    # The edge endpoint hands out a short lived bearer token without any key.
    status, body = fetch("https://edge.microsoft.com/translate/auth")
    token = body.strip()
    if status != 200 or len(token) < 50:
        raise RuntimeError(f"取 token 失败: HTTP {status} {body[:80]}")
    payload = json.dumps([{"Text": TEXT}]).encode()
    return fetch(
        "https://api-edge.cognitive.microsofttranslator.com/translate"
        "?api-version=3.0&from=en&to=zh-Hans",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )


def libretranslate():
    return fetch("https://libretranslate.com/translate",
                 data={"q": TEXT, "source": "en", "target": "zh", "format": "text"})


def baidu_transapi():
    return fetch("https://fanyi.baidu.com/transapi",
                 data={"from": "en", "to": "zh", "query": TEXT})


def transmart():
    return fetch("https://transmart.qq.com/api/imt",
                 data=json.dumps({"header": {"fn": "auto_translation"},
                                  "type": "plain", "model_category": "normal",
                                  "source": {"lang": "en", "text_list": [TEXT]},
                                  "target": {"lang": "zh"}}).encode(),
                 headers={"Content-Type": "application/json"})


CANDIDATES = [
    ("MyMemory (无 key)", mymemory),
    ("Lingva / Google 代理 (无 key)", lingva),
    ("有道 demo (无 key)", youdao_demo),
    ("有道 fanyi 旧接口 (无 key)", youdao_legacy),
    ("Google 直连 (无 key)", google_direct),
    ("微软 Bing edge token (无 key)", bing),
    ("LibreTranslate 公共实例", libretranslate),
    ("百度 transapi (无 key)", baidu_transapi),
    ("腾讯 transmart (无 key)", transmart),
]


def main() -> int:
    print("=" * 70)
    print("在线翻译接口连通性测试  en -> zh")
    print(f"测试句子: {TEXT}")
    print("=" * 70)
    results = {}
    for name, fn in CANDIDATES:
        results[name] = show(name, fn)
    print("=" * 70)
    ok = [n for n, r in results.items() if r]
    print(f"可用 {len(ok)}/{len(CANDIDATES)}: " + ("、".join(ok) if ok else "无"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
