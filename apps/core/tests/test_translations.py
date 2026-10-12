"""Guard the Indonesian catalog: complete, not fuzzy, and compiled (.mo matches .po)."""

import ast
import gettext
from pathlib import Path

from django.conf import settings

LOCALE_DIR = Path(settings.LOCALE_PATHS[0]) / "id" / "LC_MESSAGES"


def _unquote(line: str) -> str:
    return ast.literal_eval(line.strip())


def parse_po(path: Path) -> list[dict]:
    """Minimal .po reader: enough for msgctxt/msgid/msgstr, continuation lines and flags."""
    entries, entry, field = [], None, None
    for raw in path.read_text(encoding="utf-8").splitlines() + [""]:
        line = raw.strip()
        if not line:
            if entry and "msgid" in entry:
                entries.append(entry)
            entry, field = None, None
            continue
        entry = entry or {"flags": set(), "msgid": None}
        if line.startswith("#,"):
            entry["flags"].update(flag.strip() for flag in line[2:].split(","))
        elif line.startswith("#"):
            continue
        elif line.startswith(("msgctxt ", "msgid ", "msgid_plural ", "msgstr ", "msgstr[")):
            field, _, value = line.partition(" ")
            entry[field] = _unquote(value)
        elif line.startswith('"') and field:
            entry[field] += _unquote(line)
    return [e for e in entries if e["msgid"]]  # drop the header entry


def test_every_message_is_translated_and_not_fuzzy():
    entries = parse_po(LOCALE_DIR / "django.po")
    assert entries, "catalog is empty"
    untranslated = [e["msgid"] for e in entries if not (e.get("msgstr") or e.get("msgstr[0]"))]
    fuzzy = [e["msgid"] for e in entries if "fuzzy" in e["flags"]]
    assert not untranslated, f"untranslated messages: {untranslated}"
    assert not fuzzy, f"fuzzy messages: {fuzzy}"


def test_compiled_catalog_matches_source():
    with open(LOCALE_DIR / "django.mo", "rb") as handle:
        compiled = gettext.GNUTranslations(handle)
    stale = [
        e["msgid"] for e in parse_po(LOCALE_DIR / "django.po")
        if (compiled.ngettext(e["msgid"], e["msgid_plural"], 1) != e["msgstr[0]"] if "msgid_plural" in e
            else compiled.pgettext(e["msgctxt"], e["msgid"]) != e["msgstr"] if "msgctxt" in e
            else compiled.gettext(e["msgid"]) != e["msgstr"])
    ]
    assert not stale, f"django.mo is out of date; run compilemessages. Stale: {stale[:5]}"
