"""Number guard: assistant text may only contain numbers that appear in this turn's tool outputs.

Extraction understands Indian and western formats:
    ₹1.4L  ₹1.38 lakh  1,40,000  140,000  ₹2.1Cr  14%  14.1%  9.3 pts  ₹48k  3.5x
Each token becomes (value, multiplier, decimals_shown, kind).

A token matches an allowed number v if v, expressed in the token's unit, rounds to what was written:
    |v / multiplier − value| ≤ 0.5 × 10^−decimals   AND   relative error ≤ 5%
The second condition stops "₹1L" from passing for ₹1.4L. Percent tokens also try v × 100, so a
stored rate of 0.1412 allows "14%" and "14.1%".

Exempt (never checked): integers ≤ 10 written without a unit, clock times (18:00), ISO dates,
day-month dates ("15 Aug"), bare years 2000–2099, and digits inside identifiers (M014, L0003, BANK_A).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

SUFFIX_MULT = {
    "k": 1e3, "K": 1e3,
    "L": 1e5, "l": 1e5, "lakh": 1e5, "lakhs": 1e5, "lac": 1e5, "lacs": 1e5,
    "Cr": 1e7, "cr": 1e7, "crore": 1e7, "crores": 1e7,
}
PCT_SUFFIXES = {"%", "pt", "pts", "percent", "percentage points", "pp"}

_MONTHS = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
_EXEMPT_PATTERNS = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[ T]\d{2}:\d{2}(?::\d{2})?)?\b"),     # ISO date / datetime
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b"),                              # clock time
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTHS}\b(?:\s+\d{{4}})?", re.I),   # 15 Aug (2026)
    re.compile(rf"\b{_MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?\b(?:,?\s+\d{{4}})?", re.I),  # Aug 15, 2026
    re.compile(rf"\b{_MONTHS}\s+\d{{4}}\b", re.I),                             # October 2026
    re.compile(r"\{[a-z_]+\}"),                                                # template placeholders
]

_NUM = re.compile(
    r"(?<![\w.])"                                   # not part of an identifier like M014 / BANK_A
    r"(?P<cur>₹|rs\.?\s?|inr\s?)?"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:\s?(?P<suf>percentage points|percent|lakhs|lakh|lacs|lac|crores|crore|pts|pt|pp|Cr|cr|%|k|K|L|x|×)(?![A-Za-z]))?"
    r"(?![\w])",
    re.I,
)


@dataclass(frozen=True)
class NumToken:
    raw: str
    value: float          # as written (before multiplier)
    mult: float           # 1, 1e3, 1e5, 1e7
    decimals: int
    kind: str             # "inr" | "pct" | "ratio" | "plain"

    @property
    def exempt(self) -> bool:
        return self.kind == "plain" and self.mult == 1 and self.decimals == 0 and self.value <= 10


def _strip_exempt(text: str) -> str:
    for p in _EXEMPT_PATTERNS:
        text = p.sub(lambda m: " " * len(m.group(0)), text)
    return text


def extract_numbers(text: str) -> list[NumToken]:
    out = []
    for m in _NUM.finditer(_strip_exempt(text)):
        num = m.group("num")
        suf = m.group("suf") or ""
        cur = m.group("cur")
        value = float(num.replace(",", ""))
        decimals = len(num.split(".")[1]) if "." in num else 0
        suf_key = suf if suf in SUFFIX_MULT else suf.lower()
        mult = SUFFIX_MULT.get(suf, SUFFIX_MULT.get(suf_key, 1.0)) if suf not in ("x", "×") else 1.0
        if suf.lower() in PCT_SUFFIXES:
            kind = "pct"
        elif suf in ("x", "×"):
            kind = "ratio"
        elif cur or mult != 1.0:
            kind = "inr"
        else:
            kind = "plain"
        # Bare years are dates, not figures.
        if kind == "plain" and decimals == 0 and "," not in num and 2000 <= value <= 2099:
            continue
        out.append(NumToken(raw=m.group(0).strip(), value=value, mult=mult, decimals=decimals, kind=kind))
    return out


def allowed_numbers(tool_outputs: Iterable[Any]) -> set[float]:
    """Every numeric leaf in the tool outputs, plus numbers written inside their string fields."""
    vals: set[float] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            vals.add(float(x))
        elif isinstance(x, str):
            for t in extract_numbers(x):
                vals.add(t.value * t.mult)
                if t.kind == "pct":
                    vals.add(t.value / 100)
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, (list, tuple)):
            for v in x:
                walk(v)

    for o in tool_outputs:
        if isinstance(o, str):
            try:
                o = json.loads(o)
            except (ValueError, TypeError):
                pass
        walk(o)
    return vals


def _matches(tok: NumToken, v: float) -> bool:
    tol = 0.5 * 10 ** (-tok.decimals) + 1e-9
    candidates = [v / tok.mult]
    if tok.kind == "pct":
        candidates.append(v * 100)
    for c in candidates:
        if abs(c - tok.value) <= tol and (tok.value == 0 or abs(c - tok.value) / abs(tok.value) <= 0.05 or c == tok.value):
            return True
    return False


@dataclass
class GuardResult:
    ok: bool
    violations: list[str]
    checked: list[str]


def check(text: str, allowed: set[float]) -> GuardResult:
    violations, checked = [], []
    for tok in extract_numbers(text):
        if tok.exempt:
            continue
        checked.append(tok.raw)
        if not any(_matches(tok, v) for v in allowed):
            violations.append(tok.raw)
    return GuardResult(ok=not violations, violations=violations, checked=checked)


def redact(text: str, violations: list[str]) -> str:
    """Last resort after a failed retry: replace each offending figure."""
    for v in sorted(set(violations), key=len, reverse=True):
        text = text.replace(v, "[figure withheld — see card]")
    return text
