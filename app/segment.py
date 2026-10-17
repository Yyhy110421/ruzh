"""Put a hard-wrapped email or contract back together before it is translated.

Screen mode sees one subtitle line at a time, and the text tab did the same: every line
was sent to the engine on its own. That is right for a subtitle or a CSV row, where the
line really is the unit - but an email body or a clause copied out of a contract is
**hard-wrapped**. The visual line break has nothing to do with where the sentence ends:

    Срок поставки — 30 календарных дней с момента
    поступления оплаты на наш расчётный счёт.

Translated line by line, the engine is asked for "...с момента" and then for
"поступления оплаты...". Neither half carries a verb with its object, so both halves come
back wrong even though every word was recognised correctly.

So the text is rebuilt first: wrapped lines are joined back into paragraphs, and a
paragraph is translated one sentence at a time. That is the unit these engines were
trained on, and it is also what keeps a long paragraph inside the online services'
per-request limits.

Every rule below is deliberately conservative, in the same spirit as app/wordjoin.py:
joining two real paragraphs is harder to notice, and much harder to undo, than leaving
one sentence split across two requests.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Characters that may follow a terminator and still belong to the same sentence
#: (a closing quote or bracket before the space).
_CLOSERS = "\"'\u00bb\u201d\u2019)]}\uff09\u3011\u300b"

#: Terminators that end a sentence with no following space required.
_CJK_TERMINATORS = "\u3002\uff01\uff1f\uff1b"

#: A form line: a short label, a colon, then a value on the same line.
#:
#: Trade documents are full of them (单价：1200 卢布 / Наименование: Костюм), and each one
#: is a complete field. They carry no sentence terminator, so the hard-wrap rule would
#: treat every field as the continuation of the line above it and run the whole form into
#: one paragraph - which is worse than the wrapping this module exists to undo.
#:
#: The label is counted in words rather than characters. Russian labels run one to four
#: words but are longer than a Chinese one by character count, and a character cap tuned on
#: 产品名称 (4) missed Цена за единицу (15) and Минимальная партия (17) - a whole Russian
#: contract then came back merged into a single paragraph.
#:
#: A value must be present, which is what keeps a prose line introducing a list
#: ("现报价如下：", "Условия:") out of this: those lines end at the colon.
_FIELD = re.compile(r"^(\S+(?:[ \t]+\S+){0,3})[ \t]*[：:][ \t]*\S")

#: Backstop for a label written without spaces at all, as Chinese labels are.
_FIELD_LABEL_LIMIT = 24


def is_field_line(line: str) -> bool:
    """Is this a label: value line, rather than a wrapped continuation?"""
    match = _FIELD.match(line.strip())
    if not match:
        return False
    return len(match.group(1)) <= _FIELD_LABEL_LIMIT

#: Latin terminators. A dot is the only ambiguous one; ! and ? never are.
_LATIN_TERMINATORS = ".!?\u2026"

#: Words that end with a dot but do not end a sentence. Contract Russian is full of them,
#: which is why the common ones are listed rather than guessed at.
ABBREVIATIONS = {
    "\u0442.\u0435", "\u0442.\u0434", "\u0442.\u043f", "\u0442.\u043a",
    "\u0440\u0443\u0431", "\u043a\u043e\u043f", "\u0442\u044b\u0441", "\u043c\u043b\u043d",
    "\u043c\u043b\u0440\u0434", "\u0433", "\u0433\u0433", "\u0443\u043b", "\u0434",
    "\u043a\u0432", "\u043e\u0444", "\u0441\u0442\u0440", "\u0440\u0438\u0441",
    "\u0442\u0430\u0431\u043b", "\u0438\u043c", "\u043f\u0440\u043e\u0444",
    "\u0430\u043a\u0430\u0434", "\u0441\u043c", "\u043d\u0430\u043f\u0440",
    "\u043f\u0440\u0438\u043c", "\u0442\u0435\u043b", "\u0444\u0430\u043a\u0441",
    "\u043e\u0431\u043b", "\u043d\u0434\u0441", "\u0448\u0442", "\u043c\u043c", "\u043a\u0433",
    "mr", "mrs", "ms", "dr", "prof", "inc", "ltd", "co", "corp", "no", "vs", "etc",
    "eg", "ie", "approx", "fig", "dept", "jr", "sr", "pcs", "qty", "amt",
}

#: A line that opens a list item or an enumerated clause. It starts a new block even when
#: the line before it did not end with a terminator.
_BULLET = re.compile(
    r"^[ \t]*(?:[-\u2013\u2014*\u2022\u00b7\u25aa\u25cf\u25a0]"
    r"|\(?\d{1,3}[.)]|[a-zA-Z\u0430-\u044f][.)])[ \t]"
)

#: A short line with capitals but no lower-case letters reads as a heading.
_HEADING = re.compile(
    r"^(?=.*[A-Z\u0410-\u042f\u0401])[^a-z\u0430-\u044f\u0451]{0,80}$"
)

#: A line ending in one of these is still mid-sentence.
_SOFT_END = ",;:\u2014\u2013-(\u00ab\u201c"


@dataclass(frozen=True)
class Block:
    """One translation unit of the rebuilt text."""

    kind: str                 #: "para" for a paragraph, "blank" for an empty line.
    text: str
    lines: tuple[int, ...]    #: 0-based indices of the source lines it came from.


def _is_cjk(ch: str) -> bool:
    return ("\u4e00" <= ch <= "\u9fff" or "\u3000" <= ch <= "\u303f"
            or "\uff00" <= ch <= "\uffef")


def _boundary_is_cjk(a: str, b: str) -> bool:
    return bool(a) and bool(b) and (_is_cjk(a[-1]) or _is_cjk(b[0]))


def _tail_token(text: str) -> str:
    """The word immediately before the final dot, lower-cased."""
    body = text[:-1] if text.endswith(".") else text
    parts = re.split(r"[\s()\[\]{}\"'\u00ab\u00bb\u2014\u2013/,.]+", body)
    return parts[-1].lower() if parts else ""


def _dot_is_abbreviation(line: str) -> bool:
    """True when the dot ending this line belongs to an abbreviation, not a sentence."""
    token = _tail_token(line)
    if not token:
        return True                       # a lone "." is not a sentence end
    if token in ABBREVIATIONS:
        return True
    if len(token) == 1:
        return True                       # an initial: "И. Иванов"
    if token.isdigit():
        return True                       # an enumerator: "1." / "12."
    return False


def ends_sentence(line: str) -> bool:
    """Does this line finish a sentence? Blank and soft-ended lines do not."""
    stripped = line.rstrip().rstrip(_CLOSERS).rstrip()
    if not stripped:
        return False
    last = stripped[-1]
    if last in _CJK_TERMINATORS:
        return True
    if last == ".":
        return not _dot_is_abbreviation(stripped)
    if last in "!?\u2026":
        return True
    return False


def _join(previous: str, following: str) -> str:
    """Join a wrapped continuation, adding the separator the scripts call for."""
    left = previous.rstrip()
    right = following.strip()
    if not left:
        return right
    if not right:
        return left
    # A hyphen at the end of a wrapped line is a word break, not a real hyphen.
    if left.endswith("-") and len(left) > 1 and (left[-2].isalpha() or _is_cjk(left[-2])):
        return left[:-1] + right
    separator = "" if _boundary_is_cjk(left, right) else " "
    return left + separator + right


def continues_previous(previous: str, following: str) -> bool:
    """Is the following line a wrapped continuation, or the start of a new block?

    The rule lives here because two callers need the same one: the text tab joining
    hard-wrapped lines, and the document translator joining consecutive paragraphs of a
    .docx that was pasted out of a PDF and ended up one paragraph per visual line.

    A line continues the one before it only when that line did not end a sentence, and the
    new line is neither a list item, a heading, nor a form field of its own.
    """
    if not previous.strip() or not following.strip():
        return False
    if ends_sentence(previous):
        return False
    if _BULLET.match(following) or _HEADING.match(following):
        return False
    if is_field_line(following):
        return False
    # A field's value is finished where the field ends, so a complete sentence underneath
    # one is a new line rather than its remainder. A continuation without a terminator
    # still joins, which is what a value wrapped across two lines looks like.
    if is_field_line(previous) and ends_sentence(following):
        return False
    return True


def rebuild_paragraphs(text: str) -> list[Block]:
    """Group hard-wrapped lines back into paragraphs.

    Blank lines always separate paragraphs. Otherwise a line continues the previous one
    unless the previous line ended a sentence, or this line opens a list item or reads as
    a heading - the two shapes that legitimately start a block with no blank line between.
    """
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    current: list[int] = []

    def flush() -> None:
        if not current:
            return
        joined = ""
        for index in current:
            joined = _join(joined, raw[index]) if joined else raw[index].strip()
        blocks.append(Block("para", joined, tuple(current)))
        current.clear()

    for index, line in enumerate(raw):
        if not line.strip():
            flush()
            blocks.append(Block("blank", "", (index,)))
            continue
        if not current:
            current.append(index)
            continue
        if not continues_previous(raw[current[-1]], line):
            flush()
        current.append(index)
    flush()
    return blocks


def split_sentences(paragraph: str) -> list[str]:
    """Split one paragraph into sentences, keeping the original spacing.

    Each request to the engine should carry a whole sentence. Abbreviations, initials,
    decimals and enumerators do not end a sentence, and a terminator followed by a closing
    quote keeps the quote with the sentence it closes.
    """
    text = paragraph
    if not text.strip():
        return []
    sentences: list[str] = []
    start = 0
    index = 0
    length = len(text)

    while index < length:
        ch = text[index]
        if ch not in _LATIN_TERMINATORS and ch not in _CJK_TERMINATORS:
            index += 1
            continue
        cut_here = True
        if ch == ".":
            previous = text[index - 1] if index else ""
            following = text[index + 1] if index + 1 < length else ""
            if previous.isdigit() and following.isdigit():
                cut_here = False                  # a decimal point: 1.5 / 10.000,50
            elif _dot_is_abbreviation(text[start:index + 1]):
                cut_here = False
        if cut_here:
            end = index + 1
            while end < length and text[end] in _CLOSERS:
                end += 1
            # A Latin terminator only ends a sentence at the end of the paragraph or
            # before whitespace; a CJK one needs no space at all.
            if (end >= length or text[end].isspace()
                    or ch in _CJK_TERMINATORS):
                sentences.append(text[start:end].strip())
                while end < length and text[end].isspace():
                    end += 1
                start = end
                index = end
                continue
        index += 1

    tail = text[start:].strip()
    if tail:
        sentences.append(tail)
    return [sentence for sentence in sentences if sentence]


def join_units(parts: list[str]) -> str:
    """Join translated units back into one paragraph.

    The separator follows the target script, so Chinese comes back without the spaces an
    English word boundary would need.
    """
    joined = ""
    for part in parts:
        if not part:
            continue
        joined = _join(joined, part) if joined else part.strip()
    return joined
