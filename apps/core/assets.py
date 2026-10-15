"""The stylesheet pages load, built by ``manage.py build_css`` into ``static/build``:

* Tailwind CSS (the CLI pinned in ``package.json``) compiles ``frontend/app.css``: only the
  utilities the templates, scripts and Python code use are generated, plus the project's
  components and base styles.
* Bootstrap Icons keeps the icons in use (``bi-…`` classes found in the same sources) and its
  font is subset to their glyphs (WOFF2 only).
* Both go into one minified file, ``app.min.css``.

A test rebuilds in memory and fails when the committed files are stale: run
``manage.py build_css`` after using a class or icon that isn't in the build yet.
"""

import io
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
APP_CSS = ROOT / "frontend" / "app.css"
TAILWIND = ROOT / "node_modules" / ".bin" / "tailwindcss"
ICONS = ROOT / "frontend" / "vendor" / "bootstrap-icons-1.11.3"
ICONS_CSS = ICONS / "bootstrap-icons.min.css"
ICONS_FONT = ICONS / "fonts" / "bootstrap-icons.woff2"

BUILD = ROOT / "static" / "build"
OUT_CSS = BUILD / "app.min.css"
OUT_FONT = BUILD / "fonts" / "bootstrap-icons.woff2"

ICON_CLASS = re.compile(r"\bbi-[a-z0-9-]+")
# "bi-{{ icon }}" takes the values templates assign to that variable (icon="bar-chart").
ICON_VARIABLE = re.compile(r"\bbi-\{\{\s*(\w+)\s*\}\}")
ASSIGNED = re.compile(r"""\b(\w+)=["']([a-z0-9-]+)["']""")
ICON_RULE = re.compile(r"\.(bi-[a-z0-9-]+)::before\{content:\"\\([0-9a-f]+)\"\}")


class TailwindMissing(RuntimeError):
    pass


def _sources() -> list[Path]:
    files = list((ROOT / "templates").rglob("*.html"))
    files += list((ROOT / "static" / "js").rglob("*.js"))
    files += [p for p in (ROOT / "apps").rglob("*.py") if "tests" not in p.parts and "migrations" not in p.parts]
    return files


def used_icons() -> set[str]:
    icons, variables, assigned = set(), set(), {}
    for path in _sources():
        text = path.read_text(encoding="utf-8")
        icons.update(ICON_CLASS.findall(text))
        variables.update(ICON_VARIABLE.findall(text))
        for name, value in ASSIGNED.findall(text):
            assigned.setdefault(name, set()).add(value)
    icons |= {f"bi-{value}" for variable in variables for value in assigned.get(variable, ())}
    return icons


def _icons(used: set[str]) -> tuple[str, list[int]]:
    """The icon font's @font-face and base rule, plus one rule per icon in use."""
    source = ICONS_CSS.read_text(encoding="utf-8")
    rules = {name: code for name, code in ICON_RULE.findall(source)}
    kept = sorted(name for name in used if name in rules)
    face = ('@font-face{font-display:block;font-family:"bootstrap-icons";'
            'src:url("fonts/bootstrap-icons.woff2") format("woff2")}')
    base = re.search(r'\.bi::before,\[class\*=" bi-"\]::before,\[class\^=bi-\]::before\{[^}]*\}', source)[0]
    css = face + base + "".join(f'.{name}::before{{content:"\\{rules[name]}"}}' for name in kept)
    return css, sorted(int(rules[name], 16) for name in kept)


def tailwind() -> str:
    """frontend/app.css compiled and minified by the pinned Tailwind CLI (``npm ci`` installs it)."""
    if not TAILWIND.exists() or not shutil.which("node"):
        raise TailwindMissing("Tailwind CSS isn't installed: run `npm ci` (needs Node.js).")
    result = subprocess.run(
        [str(TAILWIND), "--input", str(APP_CSS), "--output", "-", "--minify"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout


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
    codepoints: list[int]


def build() -> Build:
    icons, codepoints = _icons(used_icons())
    header = "/*! Bootstrap Icons v1.11.3 (MIT, https://icons.getbootstrap.com), subset by manage.py build_css */\n"
    return Build(css=f"{tailwind().strip()}\n{header}{icons}\n", codepoints=codepoints)


def write(result: Build) -> dict[Path, int]:
    OUT_FONT.parent.mkdir(parents=True, exist_ok=True)
    OUT_CSS.write_text(result.css, encoding="utf-8")
    OUT_FONT.write_bytes(subset_font(result.codepoints))
    return {path: path.stat().st_size for path in (OUT_CSS, OUT_FONT)}
