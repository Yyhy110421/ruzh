"""Turn the FreeDict/WikDict Chinese-Russian dictionary into the app's lookup table.

Source: FreeDict `zho-rus` (WikDict, built from Wiktionary via DBnary), 156,590
headwords, **CC BY-SA 3.0 Unported** - commercial use is allowed with attribution; the
ShareAlike term applies to the dictionary data, which is why the generated file stays
under the same licence and the software merely ships it.

The source file is Chinese -> Russian only, so the reverse direction is built here by
inverting the translations. Two cleanups matter for practical use:

* Russian entries carry stress marks (``Зо́на``); the combining acute is stripped, or
  nothing would ever match what an OCR reads off a screen.
* Translations use Wiktionary link syntax (``[[по́за]] [[шестьдеся́т де́вять]]``), which
  is unwrapped.

    python scripts/build_dictionary.py             # download if needed, then build
    python scripts/build_dictionary.py --limit 200 # quick sample for inspection
"""

from __future__ import annotations

import argparse
import lzma
import re
import sys
import tarfile
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

#: Kept outside the repository's allowlist so a 90 MB source dump never gets committed.
CACHE = ROOT / "build" / "dict"
OUT = ROOT / "app" / "data" / "zh_ru_dict.db"

SOURCE_URL = ("https://download.freedict.org/dictionaries/zho-rus/2025.11.23/"
              "freedict-zho-rus-2025.11.23.src.tar.xz")

TEI_NS = "{http://www.tei-c.org/ns/1.0}"

#: Max translations kept per headword, in each direction.
MAX_TRANS = 4

#: Collected before ranking, so a common sense is not discarded just because the source
#: listed it fifth. Capping to MAX_TRANS first was a real bug: "цена" kept only
#: "上价 | 代价 | 低价" and threw "价格" away before frequency ranking ever saw it.
COLLECT_TRANS = 40

_LINK = re.compile(r"\[\[([^\]|]*)(?:\|[^\]]*)?\]\]")
_QUOTES = re.compile(r"'{2,}")
_SPACE = re.compile(r"\s+")
_NON_WORD = re.compile(r"^[^\w\u0400-\u04ff\u4e00-\u9fff]+|[^\w\u0400-\u04ff\u4e00-\u9fff]+$")
_CJK = re.compile(r"[\u4e00-\u9fff]")

#: Wiktionary's Chinese side is Traditional; the app's users read Simplified. Converted
#: at build time so the runtime needs no conversion library.
_T2S = None


def _converter():
    global _T2S
    if _T2S is None:
        from opencc import OpenCC

        _T2S = OpenCC("t2s")
    return _T2S


def simplify(text: str) -> str:
    if not _CJK.search(text):
        return text
    try:
        return _converter().convert(text)
    except Exception:  # noqa: BLE001
        return text


_ZIPF_WARNED = False


def phrase_frequency(text: str, lang: str) -> float:
    """Average word frequency of a phrase, used to rank senses.

    The dictionary lists senses in no useful order - "заказ" (an order) came back first
    as "偷工减料". Ranking translations by how common the words are in the target
    language puts 订单 above it, which is the sense a trade user wants.

    wordfreq needs ``jieba`` for Chinese tokenisation. Without it every call raises, and
    a silent fallback to 0.0 makes the ranking a no-op while still looking implemented -
    which is exactly what happened the first time. So the failure is announced.
    """
    global _ZIPF_WARNED
    try:
        from wordfreq import zipf_frequency
    except Exception as exc:  # noqa: BLE001
        if not _ZIPF_WARNED:
            _ZIPF_WARNED = True
            print(f"  ⚠ wordfreq 不可用（{exc}），词义排序将退化为原顺序")
        return 0.0
    words = [w for w in re.split(r"\s+", text.strip()) if w]
    if not words:
        return 0.0
    try:
        return sum(zipf_frequency(w, lang) for w in words) / len(words)
    except Exception as exc:  # noqa: BLE001
        if not _ZIPF_WARNED:
            _ZIPF_WARNED = True
            print(f"  ⚠ 取词频失败（{exc}）—— 中文排序需要 jieba："
                  f"pip install jieba。排序将退化为原顺序")
        return 0.0


def clean_russian(text: str) -> str:
    """Strip stress marks, Wiktionary markup and stray punctuation."""
    text = _LINK.sub(r"\1", text)
    text = _QUOTES.sub("", text)
    # Remove combining acute accents and the acute sign itself.
    text = text.replace("\u0301", "").replace("\u00b4", "").replace("'", "")
    text = unicodedata.normalize("NFC", text)
    text = _NON_WORD.sub("", text)
    return _SPACE.sub(" ", text).strip()


def clean_chinese(text: str) -> str:
    text = _LINK.sub(r"\1", text)
    text = _QUOTES.sub("", text)
    return simplify(_SPACE.sub("", text).strip())


def ensure_source(force: bool = False) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    tei = CACHE / "zho-rus.tei"
    if tei.is_file() and not force:
        return tei

    archive = CACHE / "zho-rus.src.tar.xz"
    if not archive.is_file() or force:
        print(f"下载 {SOURCE_URL}")
        request = urllib.request.Request(SOURCE_URL, headers={
            "User-Agent": "ruzh/1.0"})
        with urllib.request.urlopen(request, timeout=600) as resp, \
                open(archive, "wb") as handle:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                handle.write(chunk)
        print(f"  已保存 {archive.stat().st_size / 1048576:.1f} MB")

    print("解包…")
    with tarfile.open(archive, "r:xz") as tar:
        for member in tar.getmembers():
            if member.name.endswith("zho-rus.tei"):
                extracted = tar.extractfile(member)
                if extracted is None:
                    continue
                with open(tei, "wb") as out:
                    while True:
                        chunk = extracted.read(1 << 20)
                        if not chunk:
                            break
                        out.write(chunk)
                print(f"  已解出 {tei.stat().st_size / 1048576:.1f} MB")
                return tei
    raise SystemExit("压缩包里没有找到 zho-rus.tei")


def parse(tei: Path, limit: int | None = None) -> list[tuple[str, list[str]]]:
    """Stream the TEI and return (chinese headword, russian translations) pairs.

    The Russian text sits in ``<quote>`` inside ``<cit type="trans" xml:lang="ru">`` -
    the language attribute is on ``<cit>``, not on the quote, which is easy to get
    wrong and yields an empty result rather than an error.
    """
    entries: list[tuple[str, list[str]]] = []
    orth: str | None = None
    translations: list[str] = []
    in_entry = in_form = False

    for event, element in ET.iterparse(str(tei), events=("start", "end")):
        tag = element.tag

        if event == "start":
            if tag == f"{TEI_NS}entry":
                in_entry, orth, translations = True, None, []
            elif in_entry and tag == f"{TEI_NS}form":
                in_form = True
            continue

        if not in_entry:
            continue

        if tag == f"{TEI_NS}orth" and in_form and orth is None:
            orth = "".join(element.itertext())
        elif tag == f"{TEI_NS}form":
            in_form = False
        elif tag == f"{TEI_NS}cit":
            lang = element.attrib.get("{http://www.w3.org/XML/1998/namespace}lang")
            if element.attrib.get("type") == "trans" and lang == "ru":
                # One <quote> per translation. Joining the whole <cit> instead would
                # run them together: "T恤" became "майка тенниска тишотка футболка".
                quotes = list(element.iter(f"{TEI_NS}quote"))
                if quotes:
                    translations.extend("".join(q.itertext()) for q in quotes)
                else:
                    translations.append("".join(element.itertext()))
        elif tag == f"{TEI_NS}entry":
            in_entry = False
            if orth:
                head = clean_chinese(orth)
                cleaned: list[str] = []
                for item in translations:
                    value = clean_russian(item)
                    if value and value != head and value not in cleaned:
                        cleaned.append(value)
                if head and cleaned:
                    entries.append((head, cleaned[:COLLECT_TRANS]))
                    if limit and len(entries) >= limit:
                        element.clear()
                        break
            element.clear()

    return entries


def main() -> int:
    parser = argparse.ArgumentParser(description="生成中俄词典数据")
    parser.add_argument("--limit", type=int, default=None, help="只处理前 N 条")
    parser.add_argument("--force", action="store_true", help="重新下载")
    parser.add_argument("--dry-run", action="store_true", help="只打印样例")
    args = parser.parse_args()

    tei = ensure_source(force=args.force)
    print(f"解析 {tei.name}（{tei.stat().st_size / 1048576:.0f} MB，请稍等）…")
    entries = parse(tei, limit=args.limit)
    print(f"读出 {len(entries)} 个词条\n")

    print("样例：")
    for head, translations in entries[:12]:
        print(f"  {head:<12} -> {' | '.join(translations)}")

    # Reverse direction, skipping any Russian that maps back onto itself.
    reverse: dict[str, list[str]] = {}
    for head, translations in entries:
        for russian in translations:
            key = russian.lower()
            if not key or _CJK.search(russian):
                continue
            bucket = reverse.setdefault(key, [])
            if head not in bucket and len(bucket) < COLLECT_TRANS:
                bucket.append(head)

    # Rank both directions by how common the translation is in its own language, so the
    # first entry is the ordinary sense rather than an obscure one.
    ranked_forward: list[tuple[str, list[str]]] = []
    for head, translations in entries:
        ordered = sorted(translations, key=lambda t: phrase_frequency(t, "ru"),
                         reverse=True)
        ranked_forward.append((head, ordered[:MAX_TRANS]))
    entries = ranked_forward
    for key, chinese in reverse.items():
        reverse[key] = sorted(chinese, key=lambda t: phrase_frequency(t, "zh"),
                              reverse=True)

    print(f"\n反向索引：{len(reverse)} 个俄语词条")

    if args.dry_run:
        print("\n反向索引样例：")
        for key in list(reverse)[:10]:
            print(f"  {key:<24} -> {' | '.join(reverse[key])}")
        return 0

    lines = [
        "# 中俄词典，由 scripts/build_dictionary.py 从 FreeDict/WikDict 生成",
        "# 来源：FreeDict zho-rus (WikDict, 基于 Wiktionary/DBnary)",
        "# 授权：CC BY-SA 3.0 Unported —— 可商用，需署名，数据本身保持同一授权",
    ]

    # SQLite rather than a flat file: ~400k pairs would cost well over 100 MB of RAM if
    # loaded into dictionaries, and this machine has 8 GB total. A query per lookup
    # keeps memory flat and is fast enough at subtitle rates.
    import sqlite3

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.unlink(missing_ok=True)
    connection = sqlite3.connect(OUT)
    try:
        connection.executescript(
            # temp_store=MEMORY matters: with the default file-backed temp store, the
            # CREATE INDEX sort tries to open a temporary file and fails with
            # "unable to open database file".
            "PRAGMA temp_store=MEMORY;"
            "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);"
            "CREATE TABLE pairs (lang TEXT, term TEXT, translation TEXT, rank INT);"
        )
        connection.executemany(
            "INSERT INTO meta VALUES (?,?)",
            [("source", "FreeDict zho-rus 2025.11.23 (WikDict, Wiktionary/DBnary)"),
             ("license", "CC BY-SA 3.0 Unported"),
             ("license_url", "https://creativecommons.org/licenses/by-sa/3.0/"),
             ("built_by", "scripts/build_dictionary.py")])

        rows: list[tuple] = []
        for head, translations in entries:
            for rank, value in enumerate(translations):
                rows.append(("zh", head, value, rank))
        for russian, chinese in reverse.items():
            for rank, value in enumerate(chinese):
                rows.append(("ru", russian, value, rank))
            if len(rows) > 50000:
                connection.executemany("INSERT INTO pairs VALUES (?,?,?,?)", rows)
                rows.clear()
        if rows:
            connection.executemany("INSERT INTO pairs VALUES (?,?,?,?)", rows)
        connection.execute("CREATE INDEX idx_pairs ON pairs(lang, term)")
        connection.commit()
    finally:
        connection.close()

    total = len(entries) + len(reverse)
    print(f"\n已写入 {OUT.relative_to(ROOT)}：{total} 个词条，"
          f"{OUT.stat().st_size / 1048576:.1f} MB")
    return 0


if __name__ == "__main__":
    sys.exit(main())
