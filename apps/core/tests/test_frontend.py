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
    for kept in (".btn-primary", ".modal-header", ".badge-success", ".alert-danger", ".form-toggle", ".nav-link.active", ".icon",
                 ".grid-cols-12", ".xl\\:col-span-8", ".print\\:hidden", ".data-open\\:flex"):
        assert kept in css, kept
    for dropped in (".carousel", ".offcanvas", ".grid-cols-7", "@font-face"):
        assert dropped not in css, dropped
    assert "prefers-color-scheme:dark" in css  # light and dark follow the device


CLASS_ATTRIBUTE = re.compile(r'\bclass="([^"]*)"')
ICON_CLASSES = re.compile(r'\{%\s*icon\s+"[^"]+"\s+"([^"]*)"')
WIDGET_CLASSES = re.compile(r'"class":\s*"([^"]*)"')
BLOCK_TAG, VARIABLE_TAG = re.compile(r"\{%.*?%\}"), re.compile(r"\{\{.*?\}\}")
NOT_OURS = {"cf-turnstile"}  # Cloudflare Turnstile's own hook


def _complete_classes(value: str):
    """The class names in an attribute that are written out whole: not ``badge-{{ colour }}`` or
    ``badge-{% if … %}danger{% else %}…``, whose parts are components built from variables."""
    value = VARIABLE_TAG.sub("\x01", BLOCK_TAG.sub("\x00", value))
    for token in value.split():
        pieces = [piece for piece in token.split("\x00") if piece]
        if "\x01" not in token and len(pieces) == 1:
            yield pieces[0]


def test_every_class_in_use_is_in_the_build():
    """Catches a typo, a class left from the old framework or a component never defined: Tailwind
    generates every utility it finds, so a class missing from the build styles nothing."""
    from apps.core import assets

    css = assets.OUT_CSS.read_text(encoding="utf-8")
    defined = {re.sub(r"\\(.)", r"\1", name) for name in re.findall(r"\.((?:\\.|[A-Za-z0-9_-])+)", css)}
    sources = [*TEMPLATES.rglob("*.html"), *(Path(settings.BASE_DIR) / "apps").rglob("*.py")]
    missing = set()
    for path in sources:
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in (CLASS_ATTRIBUTE, ICON_CLASSES, WIDGET_CLASSES):
            for value in pattern.findall(text):
                missing |= {f"{path.name}: {name}" for name in _complete_classes(value) if name not in defined | NOT_OURS}
    assert not missing, sorted(missing)


# Names only the old framework has: its form classes and validation states, grid and display
# helpers, and the colours secondary/light/dark. The project's own are form-input, form-help,
# form-error, aria-invalid, Tailwind's grid and display utilities, and neutral/subtle/inactive.
OLD_VOCABULARY = re.compile(
    r"^(form-control.*|form-check.*|form-floating|(in)?valid-feedback|is-(in)?valid|input-group.*|row|g[xy]?-\d|col(-(sm|md|lg|xl|xxl))?-\d+"
    r"|d-\w+(-\w+)?|fw-\w+|fs-\d|text-bg-\w+|visually-hidden.*|btn-close|navbar.*|offcanvas.*|dropdown-(menu|item|toggle)"
    r"|(btn|btn-outline|badge|alert|table|bg|text|border|link)-(secondary|light|dark))$"
)


def test_no_class_comes_from_the_old_framework():
    """Its names, not just its files: neither used in a template, an {% icon %} or a widget, nor defined."""
    from apps.core import assets

    css = assets.OUT_CSS.read_text(encoding="utf-8")
    defined = {re.sub(r"\\(.)", r"\1", name) for name in re.findall(r"\.((?:\\.|[A-Za-z0-9_-])+)", css)}
    used = set()
    for path in [*TEMPLATES.rglob("*.html"), *(Path(settings.BASE_DIR) / "apps").rglob("*.py")]:
        if "tests" in path.parts or "migrations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in (CLASS_ATTRIBUTE, ICON_CLASSES, WIDGET_CLASSES):
            for value in pattern.findall(text):
                used |= {f"{path.name}: {name}" for name in _complete_classes(value) if OLD_VOCABULARY.match(name)}
    assert not used, sorted(used)
    assert not {name for name in defined if OLD_VOCABULARY.match(name)}
    from apps.core.templatetags.ui import STATUS_BADGES

    assert not {f"badge-{colour}" for colour in STATUS_BADGES.values()} - defined  # each status colour is a component


def test_components_use_theme_colours():
    """A colour the components need is a theme token (``bg-danger-strong``), not an arbitrary
    ``bg-[var(--app-…)]``, so utilities and components share one palette."""
    from apps.core import assets

    assert "[var(--" not in assets.APP_CSS.read_text(encoding="utf-8")


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


def test_breadcrumbs_link_each_level_and_mark_the_current_page():
    from django.template import Context, Template
    from django.utils import translation

    with translation.override("en"):
        html = Template('{% breadcrumbs %}{% crumb "Payroll" "payroll:index" year=2026 %}{% crumb "Employees" %}{% endbreadcrumbs %}').render(Context())
    assert '<nav class="breadcrumbs' in html and 'aria-label="Breadcrumb"' in html
    assert '<a href="/">Dashboard</a>' in html.split("</li>")[0]  # every trail starts at the dashboard
    assert '<a href="/payroll/?year=2026">Payroll</a>' in html and '<span aria-current="page">Employees</span>' in html
    user_data = Template("{% breadcrumbs %}{% crumb name %}{% endbreadcrumbs %}").render(Context({"name": "<b>PT X</b>"}))
    assert "&lt;b&gt;PT X&lt;/b&gt;" in user_data  # names from records are escaped


def test_sub_pages_use_breadcrumbs_not_back_links():
    offenders = [str(path.relative_to(TEMPLATES)) for path in _templates() if "&larr;" in path.read_text()]
    assert not offenders, offenders


def test_a_sub_page_shows_its_trail(auth_client):
    page = auth_client.get(reverse("payroll:employees")).content.decode()
    trail = page.split('class="breadcrumbs')[1].split("</nav>")[0]
    assert reverse("payroll:index") in trail and 'aria-current="page"' in trail


# The dashboard (where every trail starts) and pages outside the app's navigation: errors, sign-in,
# the gateway stand-ins, print views, invitations.
WITHOUT_BREADCRUMBS = {"dashboard/home.html", "403.html", "404.html", "accounts/_auth_layout.html", "billing/fake_checkout.html", "billing/fake_save.html",
                       "invoicing/invoice_print.html", "tenants/invitation.html"}


def test_every_page_of_the_app_has_breadcrumbs():
    missing = [
        name for path in _templates()
        if '{% extends "base.html" %}' in (text := path.read_text()) and "{% block breadcrumbs %}" not in text
        and (name := str(path.relative_to(TEMPLATES))) not in WITHOUT_BREADCRUMBS
    ]
    assert not missing, missing


def test_the_dashboard_has_no_trail(auth_client):
    assert 'class="breadcrumbs' not in auth_client.get(reverse("dashboard:home")).content.decode()


def test_template_comments_close_on_their_line():
    """Django's {# … #} is one line only: one that runs on is printed on the page. Use {% comment %}."""
    offenders = []
    for path in TEMPLATES.rglob("*"):
        if path.suffix not in (".html", ".txt", ".svg"):
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if re.search(r"\{#(?!.*#\})", line):
                offenders.append(f"{path.relative_to(TEMPLATES)}:{number}")
    assert not offenders, offenders


def test_page_titles_share_one_style():
    """A page of the app titles itself with .page-title (one size, set in frontend/app.css)."""
    offenders = []
    for path in _templates():
        text = path.read_text()
        if "{% block breadcrumbs %}" in text or path.name == "home.html":
            offenders += [f"{path.relative_to(TEMPLATES)}: {h1}" for h1 in re.findall(r"<h1[^>]*>", text) if "page-title" not in h1]
    assert not offenders, offenders


def test_section_headings_share_one_style():
    """A section's <h2> is .section-heading (one size, set in frontend/app.css), not a size of its own."""
    offenders = [
        f"{path.relative_to(TEMPLATES)}: {h2}" for path in _templates()
        for h2 in re.findall(r'<h2 class="text-base font-medium leading-tight(?: m[tb]-\d)?">', path.read_text())
    ]
    assert not offenders, offenders


def test_the_type_scale_and_phone_form_fields():
    """Body text 16px from one scale in rem (the browser's font size still scales it); form fields are
    16px, where iOS zooms into anything smaller; no font size is written by hand."""
    from apps.core import assets

    css = assets.OUT_CSS.read_text(encoding="utf-8")
    for token in ("--text-base:1rem", "--text-3xl:1.75rem", "--text-4xl:2.25rem"):
        assert token in css, token
    source = (Path(settings.BASE_DIR) / "frontend" / "app.css").read_text()
    assert re.search(r"--text-base:\s*1rem;", source) and "py-1.5 text-base text-fg" in source  # .form-input/-select
    assert ".form-input-sm, .form-select-sm { @apply px-2 py-1 text-base; }" in source
    handwritten = [str(p.relative_to(TEMPLATES)) for p in TEMPLATES.rglob("*.html") if re.search(r"\btext-\[[0-9.]+(rem|px|em)\]", p.read_text())]
    assert not handwritten and not re.search(r"\btext-\[[0-9.]+(rem|px|em)\]", source), handwritten
