"""The front end: Tailwind CSS built and served from this site, and pages that a strict
Content-Security-Policy lets through (no inline scripts, event handlers or styles in the templates)."""

import re
from pathlib import Path

from django.conf import settings
from django.contrib.staticfiles import finders
from django.urls import reverse

TEMPLATES = Path(settings.BASE_DIR) / "templates"
INLINE_SCRIPT = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>", re.I)
INLINE_HANDLER = re.compile(r"\son[a-z]+\s*=", re.I)
INLINE_STYLE = re.compile(r"\sstyle\s*=", re.I)


def _templates():
    return [p for p in TEMPLATES.rglob("*.html") if "emails" not in p.parts]


def test_templates_have_no_inline_scripts_handlers_or_styles():
    offenders = []
    for path in _templates():
        text = path.read_text()
        for name, pattern in (("script", INLINE_SCRIPT), ("handler", INLINE_HANDLER), ("style", INLINE_STYLE)):
            offenders += [f"{path.relative_to(TEMPLATES)}: inline {name}" for _match in pattern.finditer(text)]
    assert not offenders, offenders


def test_assets_are_served_from_this_site():
    for asset in ("build/app.min.css", "build/fonts/bootstrap-icons.woff2", "js/app.js"):
        assert finders.find(asset), asset
    assert not finders.find("build/bootstrap.bundle.min.js")  # no framework script: dialogs are native
    base = (TEMPLATES / "base.html").read_text()
    assert "cdn." not in base and "{% static 'build/app.min.css' %}" in base


# Bootstrap markup that has no meaning any more (its CSS and script are gone).
BOOTSTRAP_LEFTOVERS = re.compile(
    r"data-bs-|\b(?:d-(?:flex|none|block|grid)|col-(?:sm|md|lg|xl)-\d+|justify-content-\w+|align-items-\w+|text-secondary"
    r"|fw-(?:bold|semibold)|text-decoration-none|w-100|row g-\d|modal-dialog|btn-close|offcanvas"
    r"|dropdown-menu|list-group|page-item|text-bg-)\b"
)


def test_no_bootstrap_markup_is_left():
    offenders = [
        f"{path.relative_to(TEMPLATES)}: {match[0]}"
        for path in _templates() for match in BOOTSTRAP_LEFTOVERS.finditer(path.read_text())
    ]
    assert not offenders, offenders


def test_the_built_css_is_up_to_date():
    """Fails after using a Tailwind class or icon the build doesn't have yet: run manage.py build_css."""
    import pytest
    from fontTools.ttLib import TTFont

    from apps.core import assets

    try:
        result = assets.build()
    except assets.TailwindMissing as exc:
        pytest.skip(str(exc))
    assert assets.OUT_CSS.read_text(encoding="utf-8") == result.css, "run manage.py build_css"
    assert sorted(TTFont(str(assets.OUT_FONT)).getBestCmap()) == result.codepoints, "run manage.py build_css"


def test_the_build_has_what_pages_use_and_nothing_else():
    from apps.core import assets

    css = assets.OUT_CSS.read_text(encoding="utf-8")
    for kept in (".btn-primary", ".modal-header", ".badge-success", ".alert-danger", ".form-switch", ".nav-link.active",
                 ".grid-cols-12", ".xl\\:col-span-8", ".print\\:hidden", ".data-open\\:flex", ".bi-journal-bookmark-fill::before"):
        assert kept in css, kept
    for dropped in (".carousel", ".offcanvas", ".grid-cols-7", ".bi-alarm::before"):
        assert dropped not in css, dropped
    assert "prefers-color-scheme:dark" in css  # light and dark follow the device


def test_pages_carry_the_content_security_policy(auth_client):
    response = auth_client.get(reverse("dashboard:home"))
    policy = response["Content-Security-Policy"]
    assert "script-src 'self' https://challenges.cloudflare.com" in policy and "style-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy and "'unsafe-inline'" not in policy
    content = response.content.decode()
    assert '<dialog class="modal" id="confirmModal"' in content and 'data-drawer-open="appSidebar"' in content
