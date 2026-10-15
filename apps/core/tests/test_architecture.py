"""The layers depend inward (README "Architecture"): domain (pure rules) <- models <- selectors (reads)
<- services (writes) <- forms and views (delivery). Checked on every app module, imports inside
functions included, so a shortcut can't hide in a function body."""

import ast
from pathlib import Path

import pytest
from django.conf import settings

APPS = Path(settings.BASE_DIR) / "apps"
LAYER_FILES = ("domain", "models", "selectors", "services")
# What each layer must not import: the layers above it (of any app).
FORBIDDEN = {
    "domain": {"models", "selectors", "services", "views", "forms", "pages"},
    "models": {"selectors", "services", "views", "forms", "pages"},
    "selectors": {"services", "views", "forms", "pages"},
    "services": {"views", "forms", "pages"},
}


def _modules(layer: str):
    return sorted(path for path in APPS.glob(f"*/{layer}.py"))


def _imported_layers(path: Path) -> set[tuple[int, str]]:
    """(line, layer) of each import of a project layer module: ``from .services import x``,
    ``from apps.ledger.services import x``, ``from apps.ledger import services``."""
    found = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            parts = module.split(".")
            if node.level or parts[0] == "apps":
                found |= {(node.lineno, part) for part in parts[1 if parts[0] == "apps" else 0:]}
                if node.level and not module or parts[0] == "apps" and len(parts) == 2:
                    found |= {(node.lineno, alias.name) for alias in node.names}  # from . import services
        elif isinstance(node, ast.Import):
            found |= {(node.lineno, part) for alias in node.names if alias.name.startswith("apps.") for part in alias.name.split(".")}
    return found


@pytest.mark.parametrize("layer", list(FORBIDDEN))
def test_layers_only_depend_inward(layer):
    offenders = [
        f"{path.relative_to(APPS)}:{line} imports {name}"
        for path in _modules(layer) for line, name in sorted(_imported_layers(path)) if name in FORBIDDEN[layer]
    ]
    assert not offenders, offenders


def test_domain_rules_touch_no_database():
    offenders = [str(path.relative_to(APPS)) for path in _modules("domain") if ".objects" in path.read_text(encoding="utf-8")]
    assert not offenders, offenders


WRITES = (".objects.create(", ".objects.bulk_create(", ".objects.get_or_create(", ".objects.update_or_create(", ".update(", ".delete(")


def test_views_write_only_through_services():
    """A view parses input and calls one service; even a one-field change is a service (README)."""
    offenders = []
    for path in sorted([*APPS.glob("*/views.py"), *APPS.glob("*/pages.py")]):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if ".objects." in line and any(write in line for write in WRITES):
                offenders.append(f"{path.relative_to(APPS)}:{number}")
    assert not offenders, offenders
