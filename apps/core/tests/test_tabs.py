from django.test import RequestFactory

from apps.core.tabs import tabs

CHOICES = [("draft", "Draft"), ("void", "Void")]


def test_tabs_keep_other_parameters_and_restart_at_the_first_page():
    request = RequestFactory().get("/", {"q": "kopi", "status": "draft", "page": "3"})
    tabset = tabs(request, "status", CHOICES, {"": 5, "draft": 2, "void": 1})
    assert tabset.current == "draft"
    assert [(t.value, t.count, t.url, t.current) for t in tabset.tabs] == [
        ("", 5, "?q=kopi", False), ("draft", 2, "?q=kopi&status=draft", True), ("void", 1, "?q=kopi&status=void", False),
    ]


def test_an_optional_tab_shows_only_with_rows_or_when_chosen():
    request = RequestFactory().get("/")
    assert [t.value for t in tabs(request, "status", CHOICES, {"": 2, "draft": 2}, optional=("void",)).tabs] == ["", "draft"]
    request = RequestFactory().get("/", {"status": "void"})
    assert [t.value for t in tabs(request, "status", CHOICES, {"": 2}, optional=("void",)).tabs] == ["", "draft", "void"]


def test_an_unknown_value_selects_all():
    tabset = tabs(RequestFactory().get("/", {"status": "nope"}), "status", CHOICES, {})
    assert tabset.current == "" and tabset.tabs[0].current


def test_a_list_that_opens_on_one_status_has_no_all_tab():
    tabset = tabs(RequestFactory().get("/"), "status", CHOICES, {"draft": 2}, default="draft")
    assert tabset.current == "draft" and [(t.value, t.current) for t in tabset.tabs] == [("draft", True), ("void", False)]
