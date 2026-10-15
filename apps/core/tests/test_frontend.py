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
    for asset in ("build/app.min.css", "js/app.js"):
        assert finders.find(asset), asset
    base = (TEMPLATES / "base.html").read_text()
    assert "cdn." not in base and "{% static 'build/app.min.css' %}" in base


# Spelled apart so this file doesn't match itself.
FRAMEWORK, ITS_DATA, ITS_ICONS = "boot" + "strap", "data-" + "bs-", re.compile('class="b' + 'i\\b')
PROJECT_FILES = ("templates", "static/js", "static/build", "frontend", "apps", "config", "deploy", "requirements")


def test_no_trace_of_the_old_css_framework():
    """Tailwind CSS and Lucide icons replaced it entirely: no markup, classes, files or mentions."""
    root = Path(settings.BASE_DIR)
    offenders = [str(path.relative_to(root)) for path in root.glob("*.md") if FRAMEWORK in path.read_text().lower()]
    for folder in PROJECT_FILES:
        for path in (root / folder).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts and path.suffix not in (".pyc", ".mo"):
                text = path.read_text(encoding="utf-8", errors="ignore").lower()
                if FRAMEWORK in text or ITS_DATA in text or ITS_ICONS.search(text):
                    offenders.append(str(path.relative_to(root)))
    assert not offenders, offenders


def test_the_built_assets_are_up_to_date():
    """Fails after using a Tailwind class or an icon the build doesn't have yet: run manage.py build_css."""
    import pytest

    from apps.core import assets

    try:
        result = assets.build()
    except assets.ToolMissing as exc:
        pytest.skip(str(exc))
    assert assets.OUT_CSS.read_text(encoding="utf-8") == result.css, "run manage.py build_css"
    built = {path.name: path.read_text(encoding="utf-8") for path in assets.OUT_ICONS.iterdir()}
    assert built == result.icons, "run manage.py build_css"


def test_the_build_has_what_pages_use_and_nothing_else():
    from apps.core import assets

    css = assets.OUT_CSS.read_text(encoding="utf-8")
    for kept in (".btn-primary", ".modal-header", ".badge-success", ".alert-danger", ".form-switch", ".nav-link.active", ".icon",
                 ".grid-cols-12", ".xl\\:col-span-8", ".print\\:hidden", ".data-open\\:flex"):
        assert kept in css, kept
    for dropped in (".carousel", ".offcanvas", ".grid-cols-7", "@font-face"):
        assert dropped not in css, dropped
    assert "prefers-color-scheme:dark" in css  # light and dark follow the device


def test_icons_are_inline_svg_from_the_build():
    import pytest
    from django.template import Context, Template, TemplateSyntaxError

    html = Template('{% icon "receipt" "me-2 text-primary" %}').render(Context())
    assert html.startswith('<svg class="icon me-2 text-primary"') and 'aria-hidden="true"' in html
    with pytest.raises(TemplateSyntaxError):
        Template('{% icon "../base.html" %}').render(Context())


def test_pages_carry_the_content_security_policy(auth_client):
    response = auth_client.get(reverse("dashboard:home"))
    policy = response["Content-Security-Policy"]
    assert "script-src 'self' https://challenges.cloudflare.com" in policy and "style-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy and "'unsafe-inline'" not in policy
    content = response.content.decode()
    assert '<dialog class="modal" id="confirmModal"' in content and 'data-drawer-open="appSidebar"' in content
