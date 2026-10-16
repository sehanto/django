import pytest
from django.utils import translation

from apps.uploads.importers import IMPORTERS, Context, apply, check
from apps.uploads.services import PendingFile


@pytest.fixture(autouse=True)
def english():
    with translation.override("en"):
        yield


@pytest.fixture
def upload(organization, user):
    """``upload(kind, "csv text", options={...})``: check the file, then apply it unless ``dry``."""

    def run(kind, content, *, name="file.csv", options=None, dry=False, org=None):
        org = org or organization
        importer = IMPORTERS[kind]
        raw = {f"options-{key}": value for key, value in (options or {}).items()}
        cleaned = {}
        if importer.options_form:
            form = importer.options_form(raw, organization=org, prefix="options")
            assert form.is_valid(), form.errors
            cleaned = form.cleaned_data
        data = content.encode("utf-8") if isinstance(content, str) else content
        pending = PendingFile(kind, name, data, raw)
        context = Context(org, user, cleaned)
        return check(importer, context, pending) if dry else apply(importer, context, pending)

    return run


def messages(report):
    return [(r.number, r.outcome, r.message) for r in report.results]
