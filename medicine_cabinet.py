"""
Medicine cabinet helpers: expiry parsing, speakable dates, and safety wording.

PantryPilot tracks medicines as inventory only. It never gives dosing,
substitution, or "is this safe for me" advice; those questions get a fixed
reply that points to the label, a pharmacist, or a doctor.
"""

from __future__ import annotations

import calendar
import re
from datetime import date

NO_ADVICE = (
    "I'm sorry, I can't give medical advice. I can keep track of your medicines and when they expire, "
    "but for dosing or whether a medicine is right for you, please check the label or ask a pharmacist or doctor."
)

HOW_TO_DISPOSE = (
    "The safest way to get rid of expired medicine is a pharmacy take-back point, "
    "and don't flush it unless the label says to."
)
DISPOSAL_GUIDANCE = "Please don't use it. " + HOW_TO_DISPOSE

# Words that describe packaging, not the medicine, when searching the cabinet.
FILLER_WORDS = frozenset({
    "tablet", "tablets", "pill", "pills", "capsule", "capsules", "medicine", "medicines",
    "medication", "medications", "some", "any", "bottle", "bottles", "pack", "packs",
    "syrup", "cream", "drops", "spray",
})

_MONTHS = {m.lower(): i for i, m in enumerate(calendar.month_name) if m}
_MONTHS.update({m.lower(): i for i, m in enumerate(calendar.month_abbr) if m})
_MONTHS["sept"] = 9

# Questions that ask for medical advice rather than inventory.
_ADVICE_PATTERNS = re.compile(
    r"\b(?:dose|dosage|dosing|how (?:much|many|often)|can i (?:take|give|use|mix)|should i (?:take|give)|"
    r"safe (?:to|for)|still safe|okay to take|ok to take|side effects?|interact\w*|overdose|"
    r"pregnan\w*|breastfeed\w*|instead of|substitute|prescri\w*|diagnos\w*|treat(?:ment)? for)\b"
)


def asks_for_medical_advice(text: str) -> bool:
    """True if the text asks for dosing, safety, or treatment advice."""
    return bool(_ADVICE_PATTERNS.search((text or "").lower()))


def _end_of_month(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def parse_expiry(text: str, today: date | None = None) -> date | None:
    """Parse an expiry as printed on a pack or said aloud. Raises ValueError if unreadable.

    Month-only expiries ("EXP 03/2027", "March 2027") mean the end of that month,
    as on medicine packaging. A month with no year means its next occurrence.
    """
    today = today or date.today()
    raw = (text or "").strip().lower()
    if not raw:
        return None
    raw = re.sub(r"^(?:exp(?:iry|ires|ire)?\.?|best before|use by|in|on|at)\s+", "", raw).strip(" .")

    if m := re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", raw):
        return date(int(m[1]), int(m[2]), int(m[3]))
    if m := re.fullmatch(r"(\d{4})-(\d{1,2})", raw):
        return _end_of_month(int(m[1]), int(m[2]))
    if m := re.fullmatch(r"(\d{1,2})\s*/\s*(\d{2}|\d{4})", raw):
        year = int(m[2]) + (2000 if len(m[2]) == 2 else 0)
        return _end_of_month(year, int(m[1]))
    if raw in ("next month", "end of next month"):
        y, mo = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
        return _end_of_month(y, mo)
    if raw in ("this month", "end of this month", "end of the month", "end of month"):
        return _end_of_month(today.year, today.month)
    if m := re.fullmatch(r"(?:the\s+)?(?:end of\s+)?([a-z]+)\s*(\d{4})?", raw):
        month = _MONTHS.get(m[1])
        if month:
            if m[2]:
                return _end_of_month(int(m[2]), month)
            year = today.year if month >= today.month else today.year + 1
            return _end_of_month(year, month)
    if m := re.fullmatch(r"(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]+)\s*(\d{4})?", raw):
        month = _MONTHS.get(m[2])
        if month:
            return date(int(m[3]) if m[3] else today.year, month, int(m[1]))
    if m := re.fullmatch(r"([a-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?,?\s*(\d{4})?", raw):
        month = _MONTHS.get(m[1])
        if month:
            return date(int(m[3]) if m[3] else today.year, month, int(m[2]))
    raise ValueError(
        "I couldn't read that expiry date. Try something like March 2027, or 03 slash 2027."
    )


def spoken_date(iso: str | None) -> str:
    """'2027-03-31' → 'March 31, 2027'."""
    if not iso:
        return "no expiry date"
    d = date.fromisoformat(iso)
    return f"{d:%B} {d.day}, {d.year}"


def describe(qty: float, form: str, name: str, strength: str) -> str:
    """'2 packs of ibuprofen 200 mg'."""
    form = form or "pack"
    if qty != 1 and not form.endswith("s"):
        form += "es" if form.endswith(("sh", "ch", "x")) else "s"
    label = f"{name} {strength}".strip()
    return f"{qty:g} {form} of {label}"
