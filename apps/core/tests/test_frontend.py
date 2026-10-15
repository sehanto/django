"""The front end: Bootstrap served from this site, and pages that a strict Content-Security-Policy
lets through (no inline scripts, event handlers or styles in the templates)."""

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


def test_bootstrap_is_served_from_this_site():
    for asset in ("build/app.min.css", "build/bootstrap.bundle.min.js", "build/fonts/bootstrap-icons.woff2"):
        assert finders.find(asset), asset
    assert not finders.find("vendor/bootstrap-5.3.3/css/bootstrap.min.css")  # the full sources aren't served
    base = (TEMPLATES / "base.html").read_text()
    assert "cdn.jsdelivr.net" not in base and "{% static 'build/app.min.css' %}" in base


def test_the_built_css_is_up_to_date():
    """Fails after using a Bootstrap class or icon the build left out: run manage.py build_css."""
    from fontTools.ttLib import TTFont

    from apps.core import assets

    result = assets.build()
    assert assets.OUT_CSS.read_text(encoding="utf-8") == result.css, "run manage.py build_css"
    assert assets.OUT_JS.read_text(encoding="utf-8") == result.js, "run manage.py build_css"
    assert sorted(TTFont(str(assets.OUT_FONT)).getBestCmap()) == result.codepoints, "run manage.py build_css"


def test_the_build_keeps_what_pages_use_and_drops_the_rest():
    from apps.core import assets

    css = assets.OUT_CSS.read_text(encoding="utf-8")
    for kept in (".offcanvas-lg", ".modal-backdrop", ".dropdown-menu.show", ".form-switch", ".text-bg-success", ".alert-danger",
                 ".table-responsive", ".visually-hidden-focusable", ".bi-journal-bookmark-fill::before", ".mw-lg"):
        assert kept in css, kept
    for dropped in (".carousel", ".accordion", ".toast", ".placeholder-glow", ".bi-alarm::before"):
        assert dropped not in css, dropped
    assert css.isascii()


def test_pages_carry_the_content_security_policy(auth_client):
    response = auth_client.get(reverse("dashboard:home"))
    policy = response["Content-Security-Policy"]
    assert "script-src 'self' https://challenges.cloudflare.com" in policy and "style-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy and "'unsafe-inline'" not in policy
    content = response.content.decode()
    assert 'id="confirmModal"' in content and 'data-bs-target="#appSidebar"' in content
