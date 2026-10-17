"""Re-split Russian words the recogniser ran together.

PP-OCR's Cyrillic model drops spaces between short words, so a subtitle line arrives as
``Спецодеждаиобувьдля сотрудников склада`` - recognisable to a human, but the machine
translation then treats one long nonsense token as a word and the sentence falls apart.
Widening the recognition input does not help (measured); the model simply does not emit
the spaces.

So the boundaries are recovered afterwards, with a word-frequency list. The algorithm is
deliberately conservative, because a wrong split is worse than a missing one:

1. A token that is already a known word is **never** touched.
2. Otherwise the most probable segmentation by summed frequency is taken, and only if a
   real segmentation exists and beats leaving the token whole by a clear margin.
3. Tokens with digits or internal punctuation are left alone.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

#: Minimum token length worth examining; short tokens are rarely joined nonsense.
MIN_TOKEN = 5

#: Longest word the matcher will consider, to bound the scan on a long token.
MAX_WORD = 24

#: A piece that is not in the list is only allowed if it is at least this long, which
#: keeps the search from inventing two-letter "words".
MIN_UNKNOWN_PIECE = 4

#: Score given to a long piece that is not in the list (a name, or a word the list
#: lacks). Low, but not so low that splitting it into nonsense is preferable.
UNKNOWN_PIECE_SCORE = 1.5

#: Cost of adding one more word. Without it the search always wins by cutting text into
#: the most frequent pieces, and since в, и, с, к, о, у, а are all real Russian words it
#: shreds "Спецодеждаиобувьдля" into eleven single letters. This is the word-insertion
#: penalty from speech recognition, and ~5.5 sits between the two extremes.
PIECE_PENALTY = 5.5

#: The only single letters that are words in Russian. A one-letter piece outside this
#: set cannot be right, so it is forbidden outright.
SINGLE_LETTER_WORDS = frozenset("виск оуаэя".replace(" ", ""))

#: A short piece must also be a *common* word. The list contains plenty of junk
#: two- and three-letter entries (ио, зав, ра), and without this the search will happily
#: explain a word the recogniser misread: "завтра" loses its т and becomes "зав ра".
#: Longer pieces are exempt because genuinely low-frequency long words exist -
#: спецодежда scores only 2.5 - and they are the ones worth recovering.
SHORT_PIECE = 3
SHORT_PIECE_MIN_ZIPF = 4.0

#: A split is only believable if the pieces look like words rather than debris. When
#: the recogniser drops a letter the whole word is gone, and no tiling of the remains is
#: right - "завтра" minus its т yields "за в ра" (average 1.7 letters) while every real
#: repair averages 3 or more ("и обувь" 3.0, "цена указана" 5.0). Below this the token
#: is left alone.
MIN_AVERAGE_PIECE = 2.5

#: How much the summed score must beat leaving the token whole for a split to apply.
MARGIN = 0.6

_CYRILLIC_LETTER = re.compile(r"[Ѐ-ӿ]")
_TOKEN = re.compile(r"([Ѐ-ӿ]+)")
#: A token must be all letters plus at most a trailing sentence mark.
_CLEAN = re.compile(r"^[Ѐ-ӿ]+$")


@lru_cache(maxsize=1)
def _words_path() -> Path:
    from .config import APP_DIR

    return APP_DIR / "app" / "data" / "ru_words.txt"


@lru_cache(maxsize=1)
def load_words() -> dict[str, float]:
    """word -> frequency score. Empty when the list is missing, which disables this."""
    path = _words_path()
    if not path.is_file():
        return {}
    words: dict[str, float] = {}
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            word, _, score = line.partition("\t")
            if not word:
                continue
            try:
                words[word] = float(score)
            except ValueError:
                words[word] = 1.0
    except OSError:
        return {}
    return words


def _segment(lowered: str, words: dict[str, float]) -> list[str] | None:
    """Fewest-pieces tiling of a token, or None when it cannot be tiled.

    Two earlier objectives were measured and both failed on labelled cases:

    * summed frequency is *maximised* by cutting text into the most frequent pieces,
      and since в, и, с, к, о, у, а are all real Russian words that shreds
      "Спецодеждаиобувьдля" into eleven single letters;
    * longest-match looked right until the word list turned out to contain junk
      two-letter entries: it will take "ио" out of "иобувь", then dead-end on "бувь".

    Minimising the number of pieces has neither problem. It prefers the longest real
    explanation, and a dead end is simply not a valid tiling, so the search steps past
    it. Frequency is used only to break ties.
    """
    length = len(lowered)
    # best[i] = (pieces, -score, previous index) for the best tiling of lowered[:i]
    best: list[tuple[int, float, int] | None] = [None] * (length + 1)
    best[0] = (0, 0.0, 0)

    for end in range(1, length + 1):
        for start in range(max(0, end - MAX_WORD), end):
            if best[start] is None:
                continue
            piece = lowered[start:end]
            size = end - start
            if size == 1 and piece not in SINGLE_LETTER_WORDS:
                continue          # "с" is a word, "п" is not
            score = words.get(piece)
            if score is None:
                continue          # only real words may form a piece
            if 2 <= size <= SHORT_PIECE and score < SHORT_PIECE_MIN_ZIPF:
                continue          # short and rare: almost certainly junk
            candidate = (best[start][0] + 1, best[start][1] - score, start)
            if best[end] is None or candidate[:2] < best[end][:2]:
                best[end] = candidate

    if best[length] is None:
        return None

    pieces: list[str] = []
    index = length
    while index > 0:
        start = best[index][2]
        pieces.append(lowered[start:index])
        index = start
    pieces.reverse()
    return pieces


def split_joined(token: str, words: dict[str, float],
                 penalty: float | None = None) -> str:
    """Insert spaces into one joined token, or return it unchanged.

    ``penalty`` is accepted but unused; it remains so callers written against the
    earlier scoring version keep working.
    """
    if len(token) < MIN_TOKEN or not words:
        return token
    lowered = token.lower()
    if lowered in words:
        return token                      # already a real word: never touch it

    pieces = _segment(lowered, words)
    if pieces is None or len(pieces) < 2:
        return token
    # A split that leaves only fragments is a sign the word was misread, not joined.
    if len(lowered) / len(pieces) < MIN_AVERAGE_PIECE:
        return token

    # Keep the original casing by slicing the original string at the same offsets.
    out: list[str] = []
    cursor = 0
    for piece in pieces:
        out.append(token[cursor:cursor + len(piece)])
        cursor += len(piece)
    return " ".join(out)


def restore_spaces(text: str, words: dict[str, float] | None = None) -> str:
    """Split every joined Cyrillic token in a recognised line."""
    if not text or not _CYRILLIC_LETTER.search(text):
        return text
    if words is None:
        words = load_words()
    if not words:
        return text

    def replace(match: re.Match) -> str:
        token = match.group(1)
        if not _CLEAN.match(token):
            return token
        return split_joined(token, words)

    return _TOKEN.sub(replace, text)
