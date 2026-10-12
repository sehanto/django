"""Input normalization applied before validation (pure functions).

Sanitizing is not a substitute for validation or output escaping (Django's
template autoescaping and the ORM's parameterized queries handle those). It
removes characters that are never legitimate in business data and that cause
real problems: NUL bytes (rejected by PostgreSQL), other control characters,
and invisible or bidirectional-override characters that can make a name or
amount on an invoice display differently from what is stored ("Trojan Source").
"""

import re
import unicodedata

# Zero-width, bidi embedding/override/isolate, word joiners, soft hyphen, BOM.
_INVISIBLE = dict.fromkeys(
    map(ord, "­؜᠎​‌‍‎‏"
             "‪‫‬‭‮⁠⁡⁢⁣⁤"
             "⁦⁧⁨⁩﻿")
)
_HORIZONTAL_SPACE = re.compile(r"[^\S\n]+")
_EXTRA_BLANK_LINES = re.compile(r"\n{3,}")
_ANY_SPACE = re.compile(r"\s+")


def sanitize_text(value: str, *, multiline: bool = False) -> str:
    """Normalize free text: NFC, no control/invisible characters, tidy whitespace."""
    text = unicodedata.normalize("NFC", value).translate(_INVISIBLE)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) != "Cc")
    if multiline:
        lines = (_HORIZONTAL_SPACE.sub(" ", line).strip() for line in text.split("\n"))
        text = _EXTRA_BLANK_LINES.sub("\n\n", "\n".join(lines))
    else:
        text = _ANY_SPACE.sub(" ", text)
    return text.strip()


# Spreadsheet apps execute cells starting with these as formulas (CSV injection).
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Neutralize text cells that a spreadsheet would interpret as a formula (OWASP)."""
    if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value
