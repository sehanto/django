"""Unit tests for input normalization, validators and parameter parsing."""

import importlib
import inspect
from datetime import date, timedelta

import pytest
from django import forms
from django.apps import apps
from django.core.exceptions import ValidationError
from django.http import QueryDict
from django.utils import timezone

from apps.core.exceptions import ValidationFailed
from apps.core.forms import BaseForm, BusinessDateField, DigitsField, SanitizedFormMixin
from apps.core.params import choice_param, int_param, text_param, year_param
from apps.core.sanitizers import csv_safe, sanitize_text
from apps.core.validators import (
    EMAIL_MAX_LENGTH,
    PASSWORD_MAX_LENGTH,
    code_validator,
    ensure_business_date,
    nib_validator,
    ntpn_validator,
    phone_validator,
    tax_invoice_number_validator,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Toko   Maju  ", "Toko Maju"),
        ("Toko\x00Maju", "TokoMaju"),  # NUL (PostgreSQL rejects it)
        ("Toko\x07\x1bMaju", "TokoMaju"),  # other control characters
        ("PT‮ABC‬ Jaya", "PTABC Jaya"),  # bidi override ("Trojan Source")
        ("In​voice﻿", "Invoice"),  # zero-width space, BOM
        ("Rp 1.000", "Rp 1.000"),  # no-break space becomes a normal space
        ("Café", "Café"),  # NFC: combining accent composed
        ("Line one\nLine two", "Line one Line two"),  # single-line fields never keep newlines
    ],
)
def test_sanitize_single_line(raw, expected):
    assert sanitize_text(raw) == expected


def test_sanitize_multiline_keeps_paragraphs():
    raw = "  Jl. Malioboro  No. 12 \r\n\r\n\r\n\r\nYogyakarta\x00 \n"
    assert sanitize_text(raw, multiline=True) == "Jl. Malioboro No. 12\n\nYogyakarta"


def test_sanitize_is_idempotent():
    once = sanitize_text(" a‮ b \x00 ")
    assert sanitize_text(once) == once


@pytest.mark.parametrize(
    ("value", "expected"),
    [("=1+1", "'=1+1"), ("+62", "'+62"), ("-cmd", "'-cmd"), ("@SUM(A1)", "'@SUM(A1)"), ("Kas", "Kas"), (-500, -500)],
)
def test_csv_safe(value, expected):
    assert csv_safe(value) == expected


class SampleForm(BaseForm):
    name = forms.CharField()
    notes = forms.CharField(widget=forms.Textarea, required=False)
    email = forms.EmailField(required=False)
    secret = forms.CharField(widget=forms.PasswordInput, strip=False)


@pytest.mark.parametrize("make_data", [dict, lambda d: QueryDict(mutable=True) | d])
def test_forms_sanitize_text_but_never_passwords(make_data):
    form = SampleForm(data=make_data({
        "name": " Toko‮ Maju\x00 ", "notes": "a\r\n\r\n\r\nb ", "email": "  rina@example.com ", "secret": " p@ss​ ",
    }))
    assert form.is_valid(), form.errors
    assert form.cleaned_data["name"] == "Toko Maju"
    assert form.cleaned_data["notes"] == "a\n\nb"
    assert form.cleaned_data["email"] == "rina@example.com"
    assert form.cleaned_data["secret"] == " p@ss​ "  # passwords are used exactly as typed


def test_formset_prefixes_are_sanitized():
    formset = forms.formset_factory(SampleForm)(data={
        "form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "0", "form-0-name": "\x00 Kopi ", "form-0-secret": "x",
    })
    assert formset.is_valid(), formset.errors
    assert formset.forms[0].cleaned_data["name"] == "Kopi"


def test_every_project_form_sanitizes_input():
    """Guard: new forms must inherit BaseForm/BaseModelForm (or SanitizedFormMixin)."""
    offenders = []
    for config in apps.get_app_configs():
        if not config.name.startswith("apps."):
            continue
        try:
            module = importlib.import_module(f"{config.name}.forms")
        except ModuleNotFoundError:
            continue
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and issubclass(cls, forms.BaseForm) and not issubclass(cls, SanitizedFormMixin):
                offenders.append(f"{module.__name__}.{name}")
    assert not offenders, offenders


def test_digits_field_accepts_human_formatting():
    field = DigitsField(max_length=16)
    assert field.clean("01.234.567.8-901.000") == "012345678901000"
    assert field.clean("3201 0123 4567 8901") == "3201012345678901"


@pytest.mark.parametrize(("validator", "valid", "invalid"), [
    (phone_validator, ["0812-3456-7890", "+62 21 555 1234", "(021) 5551234"], ["call me", "12", "+62<script>"]),
    (code_validator, ["1-1000", "KOPI-ARB", "A/01.2_x"], ["-1000", "kode spasi", "x;drop"]),
    (nib_validator, ["1234567890123"], ["123", "12345678901234", "12345678901ab"]),
    (ntpn_validator, ["0A1B2C3D4E5F6G7H"], ["short", "0a1b2c3d4e5f6g7h", "0A1B2C3D4E5F6G7H9"]),
    (tax_invoice_number_validator, ["010.000-26.00000001", "04002600000001234"], ["ABC", ".010"]),
])
def test_format_validators(validator, valid, invalid):
    for value in valid:
        validator(value)
    for value in invalid:
        with pytest.raises(ValidationError):
            validator(value)


def test_business_date_window():
    ensure_business_date(date(2000, 1, 1))
    ensure_business_date(timezone.localdate() + timedelta(days=300))
    for bad in (date(1999, 12, 31), date(26, 3, 1), timezone.localdate() + timedelta(days=400)):
        with pytest.raises(ValidationFailed):
            ensure_business_date(bad)
    field = BusinessDateField()
    assert field.clean("2026-03-10") == date(2026, 3, 10)
    with pytest.raises(ValidationError):
        field.clean("0026-03-10")


def test_param_helpers():
    params = QueryDict("q=%00%20kopi%20%20arabika&status=paid&bad=%3Cscript%3E&n=12&neg=-3&year=99999&long=" + "x" * 500)
    assert text_param(params, "q") == "kopi arabika"
    assert len(text_param(params, "long")) == 100
    assert choice_param(params, "status", ["paid", "draft"]) == "paid"
    assert choice_param(params, "bad", ["paid"], default="") == ""
    assert int_param(params, "n", minimum=1) == 12
    assert int_param(params, "neg", default=None, minimum=1) is None
    assert int_param(params, "q", default=7) == 7
    assert year_param(params) == timezone.localdate().year


def _project_form_classes():
    for config in apps.get_app_configs():
        if not config.name.startswith("apps."):
            continue
        try:
            module = importlib.import_module(f"{config.name}.forms")
        except ModuleNotFoundError:
            continue
        for _name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ == module.__name__ and issubclass(cls, forms.BaseForm):
                yield cls


def test_every_text_field_is_bounded_by_its_column():
    """Guard: text never reaches the database longer than its column (a 500 instead of a form error).
    Django's EmailField accepts 320 characters; an email column holds 254."""
    offenders = []
    for cls in _project_form_classes():
        model = getattr(getattr(cls, "_meta", None), "model", None)
        for name, field in cls.base_fields.items():
            if not isinstance(field, forms.CharField) or isinstance(field.widget, forms.PasswordInput):
                continue
            column = None
            if model is not None:
                try:
                    column = model._meta.get_field(name).max_length
                except Exception:  # noqa: BLE001 - a form-only field
                    column = None
            limit = EMAIL_MAX_LENGTH if isinstance(field, forms.EmailField) else column
            if field.max_length is None or (limit is not None and field.max_length > limit):
                offenders.append(f"{cls.__module__}.{cls.__name__}.{name} ({field.max_length} > {limit})")
    assert not offenders, offenders


def test_passwords_are_bounded_even_in_djangos_forms(user):
    from apps.accounts.forms import ChangePasswordForm, LoginForm, NewPasswordForm

    for form in (LoginForm(), NewPasswordForm(user), ChangePasswordForm(user, request=None)):
        for field in form.fields.values():
            if isinstance(field.widget, forms.PasswordInput):
                assert field.max_length == PASSWORD_MAX_LENGTH
    form = NewPasswordForm(user, data={"new_password1": "x" * (PASSWORD_MAX_LENGTH + 1), "new_password2": "x" * (PASSWORD_MAX_LENGTH + 1)})
    assert not form.is_valid() and "new_password1" in form.errors
