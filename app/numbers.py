"""Keep numbers out of the translator's hands.

The offline route from Russian to Chinese goes through English, and the first hop mangles
numbers badly. Measured, feeding it the corrected input and reading the intermediate
English:

    600000 рублей   ->  "600,0 roubles"
    600 тысяч       ->  "600,0 roubles"
    от 1000 штук    ->  "from the 1,0 ..."
    по 20 штук      ->  "of 20 grand"
    500 штук        ->  the whole clause is dropped

Rewriting the separators first did not help: "600000" comes back as "600,0" just the same,
so the model is not confused by Russian formatting - it does not treat a long digit run as
a number at all. Amounts, quantities and percentages are the entire substance of a
quotation, so the digits are taken out before translation and put back afterwards.

The placeholder is [N1]. Chosen by measurement across both hops of the route, with the
same sentence each time:

    [[N1]]  ->  "[[N1] rubles"      one bracket eaten
    {{N1}}  ->  "{N1}} rubles"      likewise
    【N1】   ->  ""                    gone entirely
    [N1]    ->  "[N1] rubles"       intact, and intact again into Chinese
    <N1>    ->  "<N1> rubles"       also intact

[N1] is used because a letter between brackets is not something a price list contains by
accident. Whatever the model does to the sentence around it, the number that comes back is
the number that went in.
"""

from __future__ import annotations

import re

#: A number as documents write them: digits, optional space-separated thousands, optional
#: decimal part with either separator. "600 000", "1200", "1,5", "45".
_NUMBER = re.compile(r"\d+(?:[ \u00a0\u202f]\d{3})*(?:[.,]\d+)?")

#: Reading the placeholder back. Tolerant about a repeated or missing character inside the
#: brackets, because engines have been seen to breathe on them.
_PLACEHOLDER = re.compile(r"\[\s*N(\d+)\s*\]")

#: See the module docstring: this shape survived both hops unchanged.
def _token(index: int) -> str:
    return f"[N{index}]"


def mask(text: str) -> tuple[str, dict[str, str]]:
    """Replace every number with a placeholder. Returns the text and how to undo it.

    The same number always gets the same placeholder, so a quantity repeated through a
    document reads consistently.
    """
    if not text:
        return text, {}
    by_value: dict[str, str] = {}
    mapping: dict[str, str] = {}

    def take(match: re.Match) -> str:
        value = match.group()
        token = by_value.get(value)
        if token is None:
            token = _token(len(by_value) + 1)
            by_value[value] = token
            mapping[token] = value
        return token

    return _NUMBER.sub(take, text), mapping


def unmask(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
    """Put the numbers back, and report any placeholder the engine lost."""
    if not text or not mapping:
        return text, []
    seen: set[str] = set()

    def restore(match: re.Match) -> str:
        token = f"[N{match.group(1)}]"
        if token not in mapping:
            return match.group(0)
        seen.add(token)
        return mapping[token]

    out = _PLACEHOLDER.sub(restore, text)
    return out, [token for token in mapping if token not in seen]


# ---------------------------------------------------------------- source formatting
#: A digit, then one of the spaces Russian uses as a thousands separator, then exactly
#: three digits that end the number.
_THOUSANDS_SEPARATOR = re.compile(r"(?<=\d)[ \u00a0\u202f](?=\d{3}(?!\d))")
_DECIMAL_COMMA = re.compile(r"(?<=\d),(?=\d)")
_PASSES = 4


def normalize_russian(text: str) -> str:
    """Rewrite Russian number formatting: "600 000" to "600000", "1,5" to "1.5"."""
    if not text:
        return text
    out = text
    for _ in range(_PASSES):
        stripped = _THOUSANDS_SEPARATOR.sub("", out)
        if stripped == out:
            break
        out = stripped
    return _DECIMAL_COMMA.sub(".", out)


def normalize(text: str, source_lang: str) -> str:
    """Normalize for the language the text is in. Only Russian needs it today."""
    if (source_lang or "").strip().lower().startswith("ru"):
        return normalize_russian(text)
    return text
