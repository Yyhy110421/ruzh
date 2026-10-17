"""Verify the two reachable keyless providers: quality, language coverage, latency."""
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

CASES = [
    ("en", "zh", "We need to talk about what happened last night."),
    ("en", "zh", "The government announced new measures to reduce air pollution in major cities."),
    ("en", "zh", "I've been waiting for this moment my whole life."),
    ("en", "zh", "Please make sure the file is saved before you shut down the computer."),
    ("zh", "en", "政府宣布了减少大城市空气污染的新措施。"),
    ("ru", "zh", "Встреча перенесена на три часа дня завтра."),
    ("zh", "ru", "会议已移至明天下午三点。"),
]


def post(url, data, headers=None):
    hdrs = {"User-Agent": UA}
    if headers:
        hdrs.update(headers)
    if isinstance(data, dict):
        body = urllib.parse.urlencode(data).encode()
        hdrs.setdefault("Content-Type", "application/x-www-form-urlencoded")
    else:
        body = data
    req = urllib.request.Request(url, data=body, headers=hdrs)
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def mymemory(src, tgt, text):
    pair = f"{src}|{tgt}-CN" if tgt == "zh" else (f"{src}-CN|{tgt}" if src == "zh" else f"{src}|{tgt}")
    data = get("https://api.mymemory.translated.net/get?q="
               + urllib.parse.quote(text) + "&langpair=" + urllib.parse.quote(pair))
    return data.get("responseData", {}).get("translatedText", ""), data.get("responseStatus")


def youdao(src, tgt, text):
    to_code = {"zh": "zh-CHS", "en": "en", "ru": "ru"}.get(tgt, tgt)
    from_code = {"zh": "zh-CHS", "en": "en", "ru": "ru"}.get(src, src)
    data = post("https://aidemo.youdao.com/trans",
                {"q": text, "from": from_code, "to": to_code})
    translated = data.get("translation") or []
    return (translated[0] if translated else ""), data.get("errorCode")


PROVIDERS = [("MyMemory", mymemory), ("有道 demo", youdao)]


def main() -> int:
    for name, fn in PROVIDERS:
        print("=" * 74)
        print(name)
        print("=" * 74)
        total = 0.0
        ok = 0
        for src, tgt, text in CASES:
            t0 = time.perf_counter()
            try:
                out, status = fn(src, tgt, text)
            except urllib.error.HTTPError as exc:
                out, status = f"<HTTP {exc.code}>", "http-error"
            except Exception as exc:  # noqa: BLE001
                out, status = f"<{type(exc).__name__}: {exc}>", "error"
            dt = (time.perf_counter() - t0) * 1000
            total += dt
            good = bool(out) and not str(out).startswith("<")
            ok += good
            print(f"  {src}->{tgt}  {dt:6.0f} ms  status={status}")
            print(f"      {text}")
            print(f"   -> {out}\n")
        print(f"  成功 {ok}/{len(CASES)}，平均 {total / len(CASES):.0f} ms/句\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
