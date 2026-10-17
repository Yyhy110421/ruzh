"""Offline term lookup, backed by the bundled Chinese-Russian dictionary.

The dictionary ships as SQLite rather than a Python structure: it holds roughly 400,000
term pairs, which would cost well over 100 MB of RAM if loaded eagerly, on a machine
with 8 GB. A single indexed query per lookup keeps memory flat.

Two ways this is used:

``lookup``
    Show the translations of a term - what a dictionary is for.
``term_override``
    Replace the translator for *short* inputs. A machine translator asked for one word
    guesses from context it does not have ("заказ" came back as "Порядок"), whereas a
    dictionary entry is exactly the right answer. Longer input still goes to the
    translator, because a dictionary cannot order words into a sentence.

Attribution: the data is FreeDict/WikDict's ``zho-rus``, licensed CC BY-SA 3.0 Unported,
which permits commercial use with credit. See ``app/data/README.md``.
"""

from __future__ import annotations

import re
import sqlite3
from functools import lru_cache
from pathlib import Path

#: Inputs longer than this are sentences, not terms, and belong to the translator.
MAX_TERM_CHARS = 40
MAX_TERM_WORDS = 4

#: Language pairs the bundled dictionary covers.
SUPPORTED = {("zh", "ru"), ("ru", "zh")}

_TRAILING = re.compile(r"[\s。．.,，!！?？:：;；、\"'“”‘’()（）\[\]]+$")
_LEADING = re.compile(r"^[\s\"'“”‘’()（）\[\]]+")


def dictionary_path() -> Path:
    from .config import APP_DIR

    return APP_DIR / "app" / "data" / "zh_ru_dict.db"


@lru_cache(maxsize=1)
def _connect(path_str: str) -> sqlite3.Connection | None:
    path = Path(path_str)
    if not path.is_file():
        return None
    try:
        # Read-only: the file ships with the app and is never written at runtime.
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True,
                                     check_same_thread=False)
        connection.execute("SELECT 1 FROM pairs LIMIT 1")
        return connection
    except sqlite3.Error:
        return None


@lru_cache(maxsize=1)
def _connection() -> sqlite3.Connection | None:
    return _connect(str(dictionary_path()))


def available() -> bool:
    return _connection() is not None


def info() -> dict:
    connection = _connection()
    if connection is None:
        return {}
    try:
        meta = dict(connection.execute("SELECT key, value FROM meta"))
        count = connection.execute("SELECT COUNT(*) FROM pairs").fetchone()[0]
        meta["pairs"] = count
        return meta
    except sqlite3.Error:
        return {}


def normalize(text: str) -> str:
    text = _TRAILING.sub("", _LEADING.sub("", (text or "").strip()))
    return text.strip()


def lookup(term: str, src_lang: str, tgt_lang: str, limit: int = 6) -> list[str]:
    """Every translation the dictionary has for one term.

    The ``lang`` column names the language of ``term`` itself, not the translation, so
    the filter is on the *source* language - filtering on the target silently returns
    nothing for every query.
    """
    connection = _connection()
    if connection is None or (src_lang, tgt_lang) not in SUPPORTED:
        return []
    cleaned = normalize(term)
    if not cleaned:
        return []
    # Chinese headwords are stored as written; Russian is stored lowercased.
    candidates = [cleaned]
    if src_lang == "ru":
        candidates.insert(0, cleaned.lower())
    for candidate in candidates:
        try:
            rows = connection.execute(
                "SELECT translation FROM pairs WHERE lang=? AND term=? "
                "ORDER BY rank LIMIT ?",
                (src_lang, candidate, limit)).fetchall()
        except sqlite3.Error:
            return []
        if rows:
            return [row[0] for row in rows]
    return []


def is_short_term(text: str) -> bool:
    cleaned = normalize(text)
    if not cleaned or len(cleaned) > MAX_TERM_CHARS:
        return False
    if "\n" in cleaned:
        return False
    return len(cleaned.split()) <= MAX_TERM_WORDS


def term_override(text: str, src_lang: str, tgt_lang: str) -> str | None:
    """The dictionary answer for a short input, or None to let the translator work."""
    if not is_short_term(text):
        return None
    hits = lookup(text, src_lang, tgt_lang)
    return hits[0] if hits else None
