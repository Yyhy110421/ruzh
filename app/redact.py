"""Mask what should not leave the machine, and put it back afterwards.

Everything here is local by default - the offline engine never sends a byte anywhere -
which is what makes an online engine a decision rather than an accident. Once one is
chosen, the text goes to a third party, and a trade message is made of exactly the things
that identify a customer: company names, tax numbers, bank details, phone numbers,
e-mail addresses, website addresses.

It is not only privacy. The engine translates the company name too: ООО Ромашка comes
back as 洋甘菊有限责任公司, which is a leak and a wrong translation in one move.

So, for online engines only: swap those for placeholders, translate, swap them back.

The placeholder shape is [[C1]] and it was chosen by measurement rather than taste. One
sentence was sent through MyMemory with seven candidate markers, and only the ASCII
bracketed ones came back untouched: [[A1]], {{A1}}, [A1] and XXA1XX survived, while
【A1】, ‹A1› and 一A1一 were rearranged or split apart. The offline engine mangles all of
them, which does not matter, because it is the one engine that never needs masking.

What is deliberately not masked:

* Numbers, prices, quantities and dates. The translation is worthless without them, and
  they do not identify anybody.
* Personal names. Chinese and Russian names have no shape a regular expression can be
  sure of, and a wrong mask is worse than no mask - it silently swaps a name for a
  placeholder that never comes back.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Matches a placeholder on the way back. Loose about spaces inside the brackets, because
#: an engine that keeps the token may still breathe around it.
TOKEN_RE = re.compile(r"\[\[\s*([A-Z]\d+)\s*\]\]")

#: Russian legal forms. The lookbehind stops АО from matching the tail of ОАО.
_RU_FORM = r"(?<![А-Яа-яЁёA-Za-z])(?:ООО|ОАО|ЗАО|ПАО|ТОО|АО|ИП|LLC|Ltd\.?)"
_RU_NAME = r"[А-ЯЁA-Z][\w\-]+(?:\s+[А-ЯЁA-Z][\w\-]+){0,2}"
_ZH_COMPANY = r"[\u4e00-\u9fff]{2,20}(?:股份有限公司|有限责任公司|有限公司|集团|公司)"


@dataclass(frozen=True)
class Rule:
    """One kind of thing worth hiding."""

    code: str
    label: str
    pattern: re.Pattern
    #: A match only counts when it holds at least this many digits. It is how a phone
    #: number is told apart from a date: 2026-10-14 has the same shape and eight digits.
    min_digits: int = 0


#: Applied in this order. The narrow and unmistakable shapes go first, so an e-mail is
#: never half-eaten by the phone rule and a tax number is never eaten by the bank rule.
RULES: list[Rule] = [
    Rule("E", "邮箱", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")),
    Rule("U", "网址", re.compile(r"(?:https?://|www\.)[^\s<>\"'，。；）】]+")),
    Rule("S", "统一社会信用代码",
         re.compile(r"(?<![0-9A-Z])[0-9A-HJ-NPQRTUWXY]{18}(?![0-9A-Z])")),
    Rule("T", "税号", re.compile(r"(?<![0-9A-Za-z])(?:[0-9]{10}|[0-9]{12})(?![0-9A-Za-z])")),
    Rule("B", "银行账号", re.compile(r"(?<!\d)\d[\d\s\-]{16,36}\d(?!\d)"), min_digits=16),
    Rule("P", "电话", re.compile(r"(?<![\d+])(?:\+\d[\d\s\-()]{6,18}\d|\d[\d\s\-()]{8,18}\d)(?!\d)"),
         min_digits=10),
    Rule("C", "公司名", re.compile(
        rf"{_RU_FORM}\s*«[^»]{{2,60}}»"
        rf"|{_RU_FORM}\s+{_RU_NAME}"
        rf"|{_ZH_COMPANY}"
    )),
]


def _digits(text: str) -> int:
    return sum(1 for ch in text if ch.isdigit())


@dataclass
class Finding:
    """One thing that would be hidden, and where it sits."""

    code: str
    label: str
    text: str
    start: int
    end: int
    token: str = ""

    def describe(self) -> str:
        return f"{self.label}「{self.text}」"


@dataclass
class Redactor:
    """Finds the identifying parts of a message and swaps them for placeholders."""

    codes: set[str] = field(default_factory=lambda: {r.code for r in RULES})

    def active_rules(self) -> list[Rule]:
        return [rule for rule in RULES if rule.code in self.codes]

    def scan(self, text: str) -> list[Finding]:
        """Everything that would be hidden, in the order it appears.

        Matches are claimed left to right and never overlap, so a phone number that sits
        inside a company name does not get two placeholders.
        """
        if not text:
            return []
        found: list[Finding] = []
        taken: list[tuple[int, int]] = []
        for rule in self.active_rules():
            for match in rule.pattern.finditer(text):
                start, end = match.span()
                if rule.min_digits and _digits(match.group()) < rule.min_digits:
                    continue
                if any(start < other_end and other_start < end
                       for other_start, other_end in taken):
                    continue
                taken.append((start, end))
                found.append(Finding(rule.code, rule.label, match.group(), start, end))
        # Numbered the way the reader meets them, not the way the rules happen to be
        # ordered, so the placeholders read down the page as C1, P1, E1.
        found.sort(key=lambda f: f.start)
        counters: dict[str, int] = {}
        for finding in found:
            counters[finding.code] = counters.get(finding.code, 0) + 1
            finding.token = f"[[{finding.code}{counters[finding.code]}]]"
        return found

    def mask(self, text: str) -> tuple[str, dict[str, str]]:
        """Return the text safe to send, and the map that puts it back.

        The same value always gets the same placeholder, so a company named twice is
        translated consistently and only one thing has to be remembered.
        """
        found = self.scan(text)
        if not found:
            return text, {}
        by_text: dict[str, str] = {}
        for finding in found:
            by_text.setdefault(finding.text, finding.token)
        out = text
        for finding in sorted(found, key=lambda f: f.start, reverse=True):
            out = out[:finding.start] + by_text[finding.text] + out[finding.end:]
        return out, {token: original for original, token in by_text.items()}

    @staticmethod
    def unmask(text: str, mapping: dict[str, str]) -> tuple[str, list[str]]:
        """Put the originals back.

        Returns the text and the placeholders that never came back, because an engine is
        free to drop one and the caller has to be able to say so rather than ship a
        translation with [[C1]] sitting in the middle of it.
        """
        if not text or not mapping:
            return text, []
        seen: set[str] = set()

        def restore(match: re.Match) -> str:
            # Back to the canonical spelling before looking it up, so a token the engine
            # breathed on - [[ C1 ]] - still resolves. The map is keyed by the bracketed
            # form because that is what a human reads in the preview.
            token = f"[[{match.group(1)}]]"
            if token not in mapping:
                return match.group(0)
            seen.add(token)
            return mapping[token]

        out = TOKEN_RE.sub(restore, text)
        missing = [token for token in mapping if token not in seen]
        return out, missing
