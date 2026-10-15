"""The stylesheet and scripts pages load, built from the vendored Bootstrap (``frontend/vendor``)
by ``manage.py build_css`` into ``static/build``:

* Bootstrap's CSS keeps only the rules whose classes the project uses (like PurgeCSS): a selector
  stays when every class it needs appears somewhere in the templates, the scripts or the Python
  code (widget attributes, status colours), or is added at runtime by Bootstrap's JavaScript.
  Classes composed in templates (``text-bg-{{ … }}``) are matched by their prefix with every word
  of those sources. Classes inside ``:not(…)`` don't count: the rule applies without them.
* Bootstrap Icons keeps the icons in use, and its font is subset to their glyphs (WOFF2 only).
* ``frontend/app.css`` (the project's own rules) is appended, so pages load one stylesheet.

A test rebuilds in memory and fails when the committed files are stale: run
``manage.py build_css`` after using a class or icon that isn't in the build yet.
"""

import io
import re
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
VENDOR = ROOT / "frontend" / "vendor"
BOOTSTRAP_CSS = VENDOR / "bootstrap-5.3.3" / "css" / "bootstrap.min.css"
BOOTSTRAP_JS = VENDOR / "bootstrap-5.3.3" / "js" / "bootstrap.bundle.min.js"
ICONS_CSS = VENDOR / "bootstrap-icons-1.11.3" / "bootstrap-icons.min.css"
ICONS_FONT = VENDOR / "bootstrap-icons-1.11.3" / "fonts" / "bootstrap-icons.woff2"
APP_CSS = ROOT / "frontend" / "app.css"

BUILD = ROOT / "static" / "build"
OUT_CSS = BUILD / "app.min.css"
OUT_JS = BUILD / "bootstrap.bundle.min.js"
OUT_FONT = BUILD / "fonts" / "bootstrap-icons.woff2"

# Classes Bootstrap's JavaScript (modal, offcanvas, dropdown, alert, collapse) adds at runtime.
RUNTIME_CLASSES = {
    "show", "showing", "hiding", "fade", "collapse", "collapsing", "modal-open", "modal-backdrop", "modal-static",
    "offcanvas-backdrop", "active", "disabled",
}
# Django message levels become alert-<tag> (settings.MESSAGE_TAGS maps error to danger).
MESSAGE_TAGS = {"debug", "info", "success", "warning", "danger"}

WORD = re.compile(r"[A-Za-z0-9_-]+")
# "prefix-{{ name }}" (a plain variable) or "prefix-{{ … }}"/"prefix-{% … %}" (filters, tags).
DYNAMIC_PREFIX = re.compile(r"([a-z][a-z0-9-]*-)(?:\{\{\s*(\w+)\s*\}\}|\{[{%])")
ASSIGNED = re.compile(r"""\b(\w+)=["']([a-z0-9-]+)["']""")
CLASS = re.compile(r"\.((?:[A-Za-z0-9_-]|\\.)+)")
NOT = re.compile(r":not\((?:[^()]|\([^()]*\))*\)")
GLYPH = re.compile(r'content:\s*"\\([0-9a-fA-F]+)"')


def _sources() -> list[Path]:
    files = list((ROOT / "templates").rglob("*.html"))
    files += [p for p in (ROOT / "static" / "js").rglob("*.js")]
    files += [p for p in (ROOT / "apps").rglob("*.py") if "tests" not in p.parts and "migrations" not in p.parts]
    return files


def used_classes() -> set[str]:
    """A plain variable after a prefix (``bi-{{ icon }}``) takes the values templates assign to it
    (``icon="bar-chart"``); anything else (``text-bg-{{ s|status_badge }}``) any word of the sources."""
    words, open_prefixes, variables, assigned = set(), set(), set(), {}
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        words.update(WORD.findall(text))
        if path.suffix == ".html":
            for prefix, variable in DYNAMIC_PREFIX.findall(text):
                (variables.add((prefix, variable)) if variable else open_prefixes.add(prefix))
            for name, value in ASSIGNED.findall(text):
                assigned.setdefault(name, set()).add(value)
    words |= MESSAGE_TAGS
    composed = {prefix + word for prefix in open_prefixes for word in words}
    composed |= {prefix + value for prefix, variable in variables for value in assigned.get(variable, ())}
    return words | composed | RUNTIME_CLASSES


def _split_selectors(prelude: str) -> list[str]:
    """Split a selector list at top-level commas (not those inside ``:not(.a, .b)``)."""
    parts, depth, current = [], 0, []
    for char in prelude:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    parts.append("".join(current).strip())
    return [p for p in parts if p]


def _selector_used(selector: str, used: set[str]) -> bool:
    needed = {name.replace("\\", "") for name in CLASS.findall(NOT.sub("", selector))}
    return needed <= used


def _purge_rules(rules, used: set[str]) -> list[str]:
    import tinycss2

    out = []
    for rule in rules:
        if rule.type == "qualified-rule":
            selectors = [s for s in _split_selectors(tinycss2.serialize(rule.prelude)) if _selector_used(s, used)]
            if selectors:
                out.append(f"{','.join(selectors)}{{{tinycss2.serialize(rule.content).strip()}}}")
        elif rule.type == "at-rule":
            if rule.lower_at_keyword in ("media", "supports", "container", "layer") and rule.content is not None:
                inner = _purge_rules(tinycss2.parse_rule_list(rule.content, skip_whitespace=True, skip_comments=True), used)
                if inner:
                    out.append(f"@{rule.at_keyword} {tinycss2.serialize(rule.prelude).strip()}{{{''.join(inner)}}}")
            else:  # @font-face, @keyframes …: kept whole
                out.append(rule.serialize())
    return out


def _ascii(css: str) -> str:
    """Non-ASCII characters (the icons' private-use codepoints) as CSS escapes, like the sources."""
    return "".join(c if ord(c) < 128 else f"\\{ord(c):x}" for c in css)


def purge(css: str, used: set[str]) -> str:
    import tinycss2

    rules = tinycss2.parse_stylesheet(css, skip_whitespace=True, skip_comments=True)
    kept = _ascii("".join(_purge_rules(rules, used)))
    # Keep only the animations something still uses.
    for name in re.findall(r"@keyframes ([\w-]+)\{", kept):
        if kept.count(name) == 1:
            kept = re.sub(r"@keyframes " + re.escape(name) + r"\{(?:[^{}]|\{[^{}]*\})*\}", "", kept)
    return kept


def _icons(used: set[str]) -> tuple[str, list[int]]:
    css = purge(ICONS_CSS.read_text(encoding="utf-8"), used)
    css = re.sub(r"src:url\([^;}]*", 'src:url("fonts/bootstrap-icons.woff2") format("woff2")', css, count=1)
    return css, sorted({int(code, 16) for code in GLYPH.findall(css)})


def subset_font(codepoints: list[int]) -> bytes:
    import logging

    from fontTools import subset
    from fontTools.ttLib import TTFont

    logging.getLogger("fontTools").setLevel(logging.ERROR)  # the vendored font's harmless "extra bytes" note

    font = TTFont(str(ICONS_FONT), recalcTimestamp=False)  # the same input builds the same file
    options = subset.Options()
    options.flavor = "woff2"
    options.layout_features = []
    options.name_IDs = ["*"]  # keep the licence and family names
    subsetter = subset.Subsetter(options)
    subsetter.populate(unicodes=codepoints)
    subsetter.subset(font)
    buffer = io.BytesIO()
    font.flavor = "woff2"
    font.save(buffer)
    return buffer.getvalue()


@dataclass(frozen=True)
class Build:
    css: str
    js: str
    codepoints: list[int]


def build() -> Build:
    used = used_classes()
    bootstrap = purge(BOOTSTRAP_CSS.read_text(encoding="utf-8"), used)
    icons, codepoints = _icons(used)
    app = re.sub(r"/\*.*?\*/", "", APP_CSS.read_text(encoding="utf-8"), flags=re.S)
    app = re.sub(r"\s*\n\s*", "\n", app).strip()
    header = "/*! Bootstrap v5.3.3 and Bootstrap Icons v1.11.3 (MIT, https://getbootstrap.com), trimmed by manage.py build_css */\n"
    js = re.sub(r"\n//# sourceMappingURL=\S+\s*$", "\n", BOOTSTRAP_JS.read_text(encoding="utf-8"))
    return Build(css=f"{header}{bootstrap}\n{icons}\n{app}\n", js=js, codepoints=codepoints)


def write(result: Build) -> dict[Path, int]:
    OUT_FONT.parent.mkdir(parents=True, exist_ok=True)
    OUT_CSS.write_text(result.css, encoding="utf-8")
    OUT_JS.write_text(result.js, encoding="utf-8")
    OUT_FONT.write_bytes(subset_font(result.codepoints))
    return {path: path.stat().st_size for path in (OUT_CSS, OUT_JS, OUT_FONT)}
