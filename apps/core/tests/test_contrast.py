"""The colour tokens of frontend/app.css meet WCAG 2.2 AA in light and dark mode: 4.5:1 for text
(1.4.3), 3:1 for control borders and focus rings (1.4.11). Colours are read from the tokens and
Tailwind's own palette (oklch), so changing a token re-checks every pair it is used in."""

import math
import re
from pathlib import Path

import pytest
from django.conf import settings

ROOT = Path(settings.BASE_DIR)
APP_CSS = (ROOT / "frontend" / "app.css").read_text(encoding="utf-8")
PALETTE_FILE = ROOT / "node_modules" / "tailwindcss" / "theme.css"
TOKEN = re.compile(r"--app-([a-z0-9-]+):\s*([^;]+);")
TEXT, NON_TEXT = 4.5, 3.0
BACKGROUNDS = ("canvas", "surface", "subtle")

# (foreground, background or backgrounds, minimum)
PAIRS = [
    *((text, BACKGROUNDS, TEXT) for text in ("fg", "muted", "primary", "primary-strong", "danger", "success", "warning", "code")),
    *(("#fff", fill, TEXT) for fill in ("primary-fill", "primary-fill-strong", "danger-fill", "danger-fill-strong", "success-fill",
                                        "success-fill-strong", "neutral-fill", "neutral-fill-strong", "inactive-fill")),
    ("gray-900", "warning-fill", TEXT), ("gray-900", "warning-fill-strong", TEXT),
    ("primary-strong", "primary-subtle", TEXT),  # the active menu entry
    ("info-text", "info-subtle", TEXT), ("success-text", "success-subtle", TEXT),
    ("warning-text", "warning-subtle", TEXT), ("danger-text", "danger-subtle", TEXT),
    ("line-strong", BACKGROUNDS, NON_TEXT),  # form controls and outline buttons
    ("primary", ("canvas", "surface"), NON_TEXT),  # focus outline
]


def _palette() -> dict[str, str]:
    if not PALETTE_FILE.exists():
        pytest.skip("npm ci installs Tailwind's palette")
    text = PALETTE_FILE.read_text(encoding="utf-8")
    return dict(re.findall(r"--color-([a-z]+-\d+|white|black):\s*([^;]+);", text))


def _linear_rgb(value: str) -> tuple[float, float, float]:
    value = value.strip()
    if value.startswith("#"):
        digits = "".join(ch * 2 for ch in value[1:]) if len(value) == 4 else value[1:]
        channels = [int(digits[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        return tuple(c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels)
    lightness, chroma, hue = (float(x) for x in re.match(r"oklch\(([\d.]+)%\s+([\d.]+)\s+([\d.]+)\)", value).groups())
    lightness /= 100
    a, b = chroma * math.cos(math.radians(hue)), chroma * math.sin(math.radians(hue))
    l_, m_, s_ = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3, (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3, (
        lightness - 0.0894841775 * a - 1.2914855480 * b) ** 3
    rgb = (4.0767416621 * l_ - 3.3077115913 * m_ + 0.2309699292 * s_, -1.2684380046 * l_ + 2.6097574011 * m_ - 0.3413193965 * s_,
           -0.0041960863 * l_ - 0.7034186147 * m_ + 1.7076147010 * s_)
    return tuple(min(1.0, max(0.0, c)) for c in rgb)


class Theme:
    def __init__(self, dark: bool):
        self.dark, self.palette = dark, _palette()
        self.tokens = dict(TOKEN.findall(APP_CSS))

    def _side(self, expression: str) -> str:
        expression = expression.strip()
        if expression.startswith("light-dark("):
            inner = expression[len("light-dark("):-1]
            depth, split = 0, None
            for index, ch in enumerate(inner):
                depth += ch == "("
                depth -= ch == ")"
                if ch == "," and depth == 0:
                    split = index
                    break
            return inner[split + 1:] if self.dark else inner[:split]
        return expression

    def rgb(self, name: str, *, under=None) -> tuple[float, float, float]:
        if name.startswith("#"):
            return _linear_rgb(name)
        if name in self.palette:
            return _linear_rgb(self.palette[name])
        expression = self._side(self.tokens[name]).strip()
        if match := re.fullmatch(r"var\(--color-([a-z]+-\d+|white|black)\)", expression):
            return _linear_rgb(self.palette[match[1]])
        if match := re.fullmatch(r"color-mix\(in oklab, var\(--color-([a-z]+-\d+)\) (\d+)%, transparent\)", expression):
            colour, share = _linear_rgb(self.palette[match[1]]), int(match[2]) / 100  # over the surface it sits on
            base = under or self.rgb("surface")
            return tuple(share * c + (1 - share) * u for c, u in zip(colour, base, strict=True))
        if expression.startswith("#"):
            return _linear_rgb(expression)
        raise AssertionError(f"can't read --app-{name}: {expression}")


def _contrast(a, b) -> float:
    def luminance(rgb):
        return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]

    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


@pytest.mark.parametrize("dark", [False, True], ids=["light", "dark"])
def test_every_colour_pair_meets_wcag_aa(dark):
    theme = Theme(dark)
    failures = []
    for foreground, backgrounds, minimum in PAIRS:
        for background in (backgrounds,) if isinstance(backgrounds, str) else backgrounds:
            ratio = _contrast(theme.rgb(foreground), theme.rgb(background))
            if ratio < minimum:
                failures.append(f"{foreground} on {background}: {ratio:.2f} < {minimum}")
    assert not failures, failures


def test_templates_use_tokens_not_dark_variants():
    """Light and dark come from the tokens (and the user's choice); a dark: variant would follow only the device."""
    offenders = [str(path.relative_to(ROOT)) for path in (ROOT / "templates").rglob("*.html") if re.search(r"\bdark:", path.read_text(encoding="utf-8"))]
    css = re.sub(r"/\*.*?\*/", "", APP_CSS, flags=re.S)
    assert not offenders and not re.search(r"\bdark:", css), offenders
