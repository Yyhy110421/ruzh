"""Hunt for a free, downloadable Russian-Chinese dictionary that this machine can fetch.

Service-side glossaries are the obvious answer and the wrong one here: Google is
unreachable, Baidu wants the paid 高级版 for custom terms, and Youdao's glossary is
documented as English-Chinese. Meanwhile the user's actual need is offline 中俄 trade
terminology, so a freely licensed bilingual dictionary would be strictly better.

Candidates: WikDict (Wiktionary-derived, CC-BY-SA, published as downloadable files),
FreeDict, and whatever Wiktionary-derived datasets are mirrored on hf-mirror.
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

for stream in (sys.stdout, sys.stderr):
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HEADERS = {"User-Agent": "ruzh/1.0"}


def status(url: str, timeout: float = 20.0) -> str:
    request = urllib.request.Request(url, headers=HEADERS, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            size = resp.headers.get("Content-Length")
            extra = f"  {int(size) / 1048576:.1f} MB" if size and size.isdigit() else ""
            return f"✅ HTTP {resp.status}{extra}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return f"❌ {type(exc).__name__}"


print("=" * 78)
print("一、WikDict：从维基词典生成的可下载双语词典")
print("=" * 78)
for url in (
    "https://www.wikdict.com/",
    "https://download.wikdict.com/dictionaries/sqlite/2024-01-01/ru-zh.sqlite3",
    "https://download.wikdict.com/dictionaries/",
    "https://www.wikdict.com/page/downloads",
):
    print(f"  {status(url):<28} {url}")

print()
print("=" * 78)
print("二、FreeDict（可达，查有没有俄中）")
print("=" * 78)
for url in (
    "https://download.freedict.org/dictionaries/",
    "https://freedict.org/freedict-database.json",
):
    print(f"  {status(url):<28} {url}")
try:
    request = urllib.request.Request("https://freedict.org/freedict-database.json",
                                     headers=HEADERS)
    with urllib.request.urlopen(request, timeout=25) as resp:
        data = json.loads(resp.read().decode("utf-8", "replace"))
    pairs = []
    for entry in data if isinstance(data, list) else []:
        name = str(entry.get("name") or "")
        if "rus" in name or "zho" in name or "chi" in name:
            pairs.append(name)
    print(f"  含俄语或中文的词典：{pairs or '（无）'}")
except Exception as exc:  # noqa: BLE001
    print(f"  读取失败：{type(exc).__name__}")

print()
print("=" * 78)
print("三、hf-mirror 上搜俄中词典数据集")
print("=" * 78)
for term in ("russian chinese dictionary", "ru zh dictionary", "russian chinese parallel",
             "wikipedia dictionary ru zh", "wikdict"):
    url = f"https://hf-mirror.com/api/datasets?search={urllib.parse.quote(term)}&limit=10"
    try:
        request = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(request, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as exc:  # noqa: BLE001
        print(f"\n[{term}] 失败：{type(exc).__name__}")
        continue
    items = data if isinstance(data, list) else data.get("datasets", [])
    print(f"\n[{term}] {len(items)} 个结果")
    for item in items[:10]:
        print(f"   {item.get('id')}")
