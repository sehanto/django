"""What pages load, built by ``manage.py build_css`` from the pinned npm packages (``npm ci``):

* ``static/build/app.min.css``: Tailwind CSS compiles ``frontend/app.css``; only the utilities the
  templates, scripts and Python code use are generated, plus the project's components and base styles.
* ``templates/icons/<name>.svg``: the Lucide icons (``lucide-static``, ISC) that templates use
  through ``{% icon "name" %}`` (``apps/core/templatetags/icons.py``), as small SVG templates.

A test rebuilds in memory and fails when the committed files are stale: run
``manage.py build_css`` after using a class or icon that isn't in the build yet.
"""

import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from django.conf import settings

ROOT = Path(settings.BASE_DIR)
APP_CSS = ROOT / "frontend" / "app.css"
TAILWIND = ROOT / "node_modules" / ".bin" / "tailwindcss"
LUCIDE = ROOT / "node_modules" / "lucide-static"

OUT_CSS = ROOT / "static" / "build" / "app.min.css"
OUT_ICONS = ROOT / "templates" / "icons"

# {% icon "receipt" … %}, or {% icon name … %} with the values templates assign to name (icon="bank").
ICON_LITERAL = re.compile(r"""\{%\s*icon\s+["']([a-z0-9-]+)["']""")
ICON_VARIABLE = re.compile(r"\{%\s*icon\s+(\w+)")
ASSIGNED = re.compile(r"""\b(\w+)=["']([a-z0-9-]+)["']""")
SVG_BODY = re.compile(r"<svg[^>]*>(.*)</svg>", re.S)
SVG_OPEN = ('<svg class="icon{% if css %} {{ css }}{% endif %}" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">')


class ToolMissing(RuntimeError):
    pass


def _installed(path: Path) -> None:
    if not path.exists() or not shutil.which("node"):
        raise ToolMissing("The front-end tools aren't installed: run `npm ci` (needs Node.js).")


def used_icons() -> set[str]:
    names, variables, assigned = set(), set(), {}
    for path in (ROOT / "templates").rglob("*.html"):
        text = path.read_text(encoding="utf-8")
        names.update(ICON_LITERAL.findall(text))
        variables.update(ICON_VARIABLE.findall(text))
        for name, value in ASSIGNED.findall(text):
            assigned.setdefault(name, set()).add(value)
    return names | {value for variable in variables for value in assigned.get(variable, ())}


def icons() -> dict[str, str]:
    """Each icon in use as a template: Lucide's paths in one <svg> with the project's attributes."""
    _installed(LUCIDE)
    version = __import__("json").loads((LUCIDE / "package.json").read_text())["version"]
    out = {}
    for name in sorted(used_icons()):
        source = LUCIDE / "icons" / f"{name}.svg"
        if not source.exists():
            raise ValueError(f"No Lucide icon named {name!r}")
        body = " ".join(line.strip() for line in SVG_BODY.search(source.read_text(encoding="utf-8"))[1].splitlines() if line.strip())
        out[f"{name}.svg"] = f"{{# Lucide {name} v{version} (ISC); built by manage.py build_css #}}{SVG_OPEN}{body}</svg>\n"
    out["LICENSE.txt"] = (LUCIDE / "LICENSE").read_text(encoding="utf-8")
    return out


def tailwind() -> str:
    """frontend/app.css compiled and minified by the pinned Tailwind CLI."""
    _installed(TAILWIND)
    result = subprocess.run(
        [str(TAILWIND), "--input", str(APP_CSS), "--output", "-", "--minify"],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip() + "\n"


@dataclass(frozen=True)
class Build:
    css: str
    icons: dict[str, str]


def build() -> Build:
    return Build(css=tailwind(), icons=icons())


def write(result: Build) -> dict[Path, int]:
    OUT_CSS.parent.mkdir(parents=True, exist_ok=True)
    OUT_CSS.write_text(result.css, encoding="utf-8")
    OUT_ICONS.mkdir(parents=True, exist_ok=True)
    for stale in OUT_ICONS.glob("*.svg"):
        if stale.name not in result.icons:
            stale.unlink()
    for name, svg in result.icons.items():
        (OUT_ICONS / name).write_text(svg, encoding="utf-8")
    sizes = {OUT_CSS: OUT_CSS.stat().st_size}
    sizes[OUT_ICONS] = sum(p.stat().st_size for p in OUT_ICONS.glob("*.svg"))
    return sizes
