"""Term glossary: the user's own words, applied around translation.

A screen translator is judged on names and jargon, not on general prose - a film's
character names, a game's item names, a talk's technical terms. Machine translation
will not know those, and the fix is a term list the user controls.

Two directions of patching are supported:

``target`` (the default)
    Replace terms in the *translation*. "GPU" -> "显卡", a character name, a product.
``source``
    Normalise the *recognised text* before it is translated, which is how you help a
    weak offline model: expand an abbreviation, or fix a spelling the model mangles
    ("Kubernates" -> "Kubernetes").

Rules live in a plain JSON file so they can be hand-edited, and can be imported and
exported as TSV so they can be maintained in Excel.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

#: Which side of the translation a rule edits.
SOURCE = "source"
TARGET = "target"

#: How a rule matches.
LITERAL = "literal"
REGEX = "regex"

_MISSING = object()

#: How the boolean columns are written. Anything else - a blank, a typo, another
#: language - reads as false, so a damaged column can only switch a rule off, never on.
_TRUE = {"1", "true", "yes", "y", "on", "是", "开", "启用"}


def _flag(value: str) -> bool:
    return value.strip().lower() in _TRUE


#: Column headings a customer's sheet is likely to start with. Only the first non-empty
#: row is ever tested against this, so a term that happens to be spelled like one of
#: them still imports when it appears further down.
_HEADER_LABELS = {
    "原文", "译文", "术语", "词条", "原词", "对照", "中文", "俄文", "翻译",
    "原语言", "目标语言", "中文术语", "俄语术语", "中文", "俄语",
    "термин", "перевод", "слово", "фраза", "значение",
    "term", "source", "target", "translation", "original",
}


def _is_header(row: list[str]) -> bool:
    """Is this first row a pair of column headings rather than a term?

    Measured need: without this, a customer's sheet imported a rule whose term was
    the word 原文 and whose translation was 译文 - one useless rule, and a glossary
    that looks like it worked.
    """
    for cell in row[:2]:
        label = cell.strip().lower().rstrip(":：")
        if label and label in _HEADER_LABELS:
            return True
    return False

#: A spreadsheet cell reference, as xlsx stores it.
_CELL_REF = re.compile(r"^([A-Z]+)([0-9]+)$")


def _column_number(letters: str) -> int:
    """A -> 1, B -> 2, AA -> 27."""
    number = 0
    for ch in letters:
        number = number * 26 + (ord(ch) - ord("A") + 1)
    return number


def _rows_from_csv(path: Path) -> list[list[str]]:
    """Every row of a CSV file.

    Through the csv module rather than by splitting on commas, because a term can contain
    a comma and a spreadsheet will quote it when it does.

    Encoding has to be guessed, which is why .xlsx is the better path and .csv the legacy
    one - a workbook is UTF-8 inside and needs no guess at all. UTF-8 is tried first
    because it validates itself: a wrong guess fails outright rather than producing text.
    GBK comes before cp1251 because both decode almost any byte sequence, so whichever is
    tried first wins, and a term list from a Chinese machine is the likelier of the two
    here. A Cyrillic list saved as cp1251 from an old Russian Excel still reads right,
    because UTF-8 and GBK both reject those bytes.
    """
    import csv

    for encoding in ("utf-8-sig", "utf-8", "gbk", "cp1251"):
        try:
            with path.open("r", encoding=encoding, newline="") as handle:
                return [list(row) for row in csv.reader(handle)]
        except UnicodeDecodeError:
            continue
    raise GlossaryError(f"读不了 {path}：编码不认识（试过 UTF-8、GBK、cp1251）")


def _rows_from_xlsx(path: Path) -> list[list[str]]:
    """Every row of the first sheet, as text.

    Reuses the workbook reader the document translator already has. Imported inside the
    function because xlsx_io pulls in the translation pipeline, which imports this module.
    """
    from .xlsx_io import read_cells

    grid: dict[int, dict[int, str]] = {}
    for reference, text in read_cells(path).items():
        match = _CELL_REF.match(reference.upper())
        if not match:
            continue
        row = int(match.group(2))
        grid.setdefault(row, {})[_column_number(match.group(1))] = text

    rows: list[list[str]] = []
    for number in sorted(grid):
        cells = grid[number]
        rows.append([cells.get(index, "") for index in range(1, max(cells) + 1)])
    return rows

#: Used to tell a script that inflects from one that does not.
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def _match_key(term: str) -> str:
    """What to look for in the source text when checking a rule's condition.

    Russian inflects, so a term in a real document is rarely the dictionary form a rule
    was written with: "спецодежда" arrives as "спецодежды", "заказ" as "заказа". Matching
    the exact string misses almost every real occurrence, which is silent - the rule
    simply never helps.

    So the tail is trimmed. It is deliberately loose, and it is safe to be loose here
    because the condition is only the first of two: the rule must also find its own key in
    the translation before it changes anything, and that second condition is what stops a
    rule from rewriting a sentence it has nothing to do with. Chinese does not inflect, so
    a term written in it is matched as it stands.
    """
    text = term.strip().lower()
    if _CJK.search(text):
        return text
    if len(text) >= 7:
        return text[:-2]
    if len(text) >= 4:
        return text[:-1]
    return text


@dataclass
class Rule:
    src: str = ""
    dst: str = ""
    mode: str = LITERAL
    side: str = TARGET
    case_sensitive: bool = True
    #: Optional list like ``["en->zh", "ru->zh"]``; empty means every pair.
    pairs: list[str] = field(default_factory=list)
    note: str = ""
    enabled: bool = True
    #: Only fire when the *source* text contains this. Without it a target rule fires on
    #: any translation that happens to contain src, and that is how a glossary turns into
    #: a source of errors: the offline engine renders "артикул" as "产材" in a table header
    #: and as "电动" on its own, and a rule written for the second one would rewrite the
    #: word "电动" wherever it legitimately appeared.
    when: str = ""

    def applies_to(self, src_lang: str, tgt_lang: str, side: str) -> bool:
        if not self.enabled or self.side != side or not self.src:
            return False
        if not self.pairs:
            return True
        return f"{src_lang}->{tgt_lang}" in self.pairs


class GlossaryError(ValueError):
    pass


def trade_glossary_path() -> Path:
    """The China-Russia trade glossary that ships with the app."""
    return Path(__file__).resolve().parent / "data" / "trade_glossary.json"


def load_with_builtin(user_path: str | os.PathLike,
                      include_builtin: bool = True) -> "Glossary":
    """The built-in trade glossary followed by the user's own rules.

    Order matters: rules are applied in sequence and a later rule wins, so the user's
    file goes last and can override anything shipped. A missing built-in file is not an
    error - a source checkout without it simply has no built-in terms.
    """
    rules: list[Rule] = []
    problems: list[str] = []

    if include_builtin:
        path = trade_glossary_path()
        if path.is_file():
            try:
                builtin = Glossary.load(path)
                rules.extend(builtin.rules)
            except GlossaryError as exc:
                problems.append(f"内置外贸词库无法读取：{exc}")

    user = Glossary.load(user_path)
    rules.extend(user.rules)

    combined = Glossary(rules, Path(user_path))
    combined.problems = problems + user.problems
    return combined


class Glossary:
    """A loaded, compiled set of rules."""

    def __init__(self, rules: list[Rule] | None = None, path: str | os.PathLike | None = None):
        self.path = Path(path) if path else None
        self.rules: list[Rule] = list(rules or [])
        self.problems: list[str] = []
        self._compiled: dict[int, re.Pattern] = {}
        self.compile()

    # -- loading / saving --------------------------------------------------
    @classmethod
    def load(cls, path: str | os.PathLike) -> "Glossary":
        path = Path(path)
        if not path.is_file():
            return cls([], path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise GlossaryError(f"词库文件无法解析：{exc}") from exc
        raw = data.get("rules") if isinstance(data, dict) else data
        rules: list[Rule] = []
        problems: list[str] = []
        for index, item in enumerate(raw or []):
            if not isinstance(item, dict):
                problems.append(f"第 {index + 1} 条不是对象，已跳过")
                continue
            known = {f for f in Rule.__dataclass_fields__}
            unknown = set(item) - known
            if unknown:
                problems.append(f"第 {index + 1} 条有未知字段 {sorted(unknown)}，已忽略")
            try:
                rules.append(Rule(**{k: v for k, v in item.items() if k in known}))
            except Exception as exc:  # noqa: BLE001
                problems.append(f"第 {index + 1} 条字段有问题：{exc}")
        glossary = cls(rules, path)
        glossary.problems = problems
        return glossary

    def save(self, path: str | os.PathLike | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise GlossaryError("没有指定词库文件路径")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "rules": [asdict(r) for r in self.rules]}
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, target)
        self.path = target
        return target

    # -- rule management ---------------------------------------------------
    def compile(self) -> None:
        """Compile regex rules once, and record the ones that will not compile."""
        self._compiled.clear()
        bad: list[str] = []
        for index, rule in enumerate(self.rules):
            if rule.mode != REGEX or not rule.src:
                continue
            flags = 0 if rule.case_sensitive else re.IGNORECASE
            try:
                self._compiled[index] = re.compile(rule.src, flags)
            except re.error as exc:
                bad.append(f"第 {index + 1} 条正则无效（{rule.src}）：{exc}")
                rule.enabled = False
        self.problems = [p for p in self.problems if "正则无效" not in p] + bad

    def add(self, rule: Rule) -> None:
        self.rules.append(rule)
        self.compile()

    def remove(self, index: int) -> None:
        del self.rules[index]
        self.compile()

    def replace_all(self, rules: list[Rule]) -> None:
        self.rules = list(rules)
        self.compile()

    # -- applying ----------------------------------------------------------
    def rules_for(self, src_lang: str, tgt_lang: str, side: str) -> list[tuple[int, Rule]]:
        return [(i, r) for i, r in enumerate(self.rules)
                if r.applies_to(src_lang, tgt_lang, side)]

    def apply(self, text: str, src_lang: str, tgt_lang: str, side: str,
              source: str | None = None) -> str:
        """Apply every matching rule once, in file order.

        source is the text this output came from. Give it and a rule carrying a when
        condition only fires if that text contains it - matched without regard to case,
        because a term sits capitalised in a header and lower case inside a sentence.
        """
        if not text:
            return text
        out = text
        lowered = source.lower() if source else None
        for index, rule in self.rules_for(src_lang, tgt_lang, side):
            if rule.when:
                if lowered is None or _match_key(rule.when) not in lowered:
                    continue
            if rule.mode == REGEX:
                pattern = self._compiled.get(index)
                if pattern is None:
                    continue
                out = pattern.sub(rule.dst, out)
            else:
                if rule.case_sensitive:
                    out = out.replace(rule.src, rule.dst)
                else:
                    out = re.sub(re.escape(rule.src), lambda _m: rule.dst, out, flags=re.IGNORECASE)
        return out

    def apply_source(self, text: str, src_lang: str, tgt_lang: str) -> str:
        return self.apply(text, src_lang, tgt_lang, SOURCE)

    def apply_target(self, text: str, src_lang: str, tgt_lang: str,
                     source: str | None = None) -> str:
        return self.apply(text, src_lang, tgt_lang, TARGET, source=source)

    # -- TSV interop -------------------------------------------------------
    def import_tsv(self, path: str | os.PathLike, side: str = TARGET,
                   case_sensitive: bool = True) -> int:
        """Import the file export_tsv writes, or a bare 原文<TAB>译文 list.

        The side and case_sensitive arguments are the defaults for a plain two-column file,
        which is what a customer's own spreadsheet looks like.
        """
        added = 0
        first_row = True
        for line_no, raw in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            row = re.split(r"\t+", line)
            if first_row:
                first_row = False
                if _is_header(row):
                    continue
            if len(row) < 2:
                self.problems.append(f"{path}:{line_no} 不是「原文<TAB>译文」，已跳过")
                continue
            rule = self._rule_from(row, side, case_sensitive)
            if rule is None:
                self.problems.append(f"{path}:{line_no} 缺原文或译文，已跳过")
                continue
            self.rules.append(rule)
            added += 1
        self.compile()
        return added

    def import_table(self, path: str | os.PathLike, side: str = TARGET,
                     case_sensitive: bool = True) -> int:
        """Import a term list in whatever format the customer sent it in.

        A customer's glossary arrives as a spreadsheet - two columns, their term and the
        translation they want - and asking them to convert it to tab-separated text first
        is exactly the kind of friction that turns a delivered product back into a
        support ticket. So .tsv, .csv and .xlsx are all read here.
        """
        suffix = Path(path).suffix.lower()
        if suffix in (".tsv", ".txt"):
            return self.import_tsv(path, side=side, case_sensitive=case_sensitive)
        if suffix == ".csv":
            rows = _rows_from_csv(Path(path))
        elif suffix in (".xlsx", ".xlsm"):
            rows = _rows_from_xlsx(Path(path))
        else:
            raise GlossaryError(f"不认识的文件格式「{suffix}」，请用 .tsv、.csv 或 .xlsx")

        added = 0
        first_row = True
        for row in rows:
            if not any(cell.strip() for cell in row):
                continue
            if first_row:
                first_row = False
                if _is_header(row):
                    continue
            rule = self._rule_from(row, side, case_sensitive)
            if rule is None:
                continue
            self.rules.append(rule)
            added += 1
        self.compile()
        return added

    def _rule_from(self, row: list[str], side: str,
                   case_sensitive: bool) -> "Rule | None":
        """One row of a table to one rule, or None when it is not a pair.

        Every column is read, not just the two ends. Writing seven and reading two made
        export and import non-inverses, and the damage was silent both ways: a regex rule
        came back literal (so it searched for the pattern as text), a source-side rule came
        back patching the translation, and a rule the customer had switched off came back
        on. The per-customer glossary is what this product is sold on.
        """
        if len(row) < 2 or not row[0].strip() or not row[1].strip():
            return None

        def column(index: int, fallback: str) -> str:
            return row[index].strip() if len(row) > index else fallback

        mode = column(2, LITERAL).lower()
        if mode not in (LITERAL, REGEX):
            if mode:
                self.problems.append(f"模式「{mode}」不认识，按字面处理")
            mode = LITERAL
        where = column(3, side).lower()
        if where not in (SOURCE, TARGET):
            if where:
                self.problems.append(f"作用侧「{where}」不认识，按译文处理")
            where = side
        return Rule(
            src=row[0].strip(),
            dst=row[1].strip(),
            mode=mode,
            side=where,
            case_sensitive=_flag(column(4, "1" if case_sensitive else "0")),
            enabled=_flag(column(5, "1")),
            when=column(6, ""),
        )

    def export_tsv(self, path: str | os.PathLike, side: str | None = None) -> int:
        lines = ["# 原文\t译文\t模式\t作用侧\t区分大小写\t生效\t仅当原文含"]
        count = 0
        for rule in self.rules:
            if side and rule.side != side:
                continue
            lines.append("\t".join([rule.src, rule.dst, rule.mode, rule.side,
                                    "1" if rule.case_sensitive else "0",
                                    "1" if rule.enabled else "0", rule.when]))
            count += 1
        Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
        return count

    def stats(self) -> dict:
        return {
            "total": len(self.rules),
            "enabled": sum(1 for r in self.rules if r.enabled),
            "source": sum(1 for r in self.rules if r.side == SOURCE),
            "target": sum(1 for r in self.rules if r.side == TARGET),
        }
