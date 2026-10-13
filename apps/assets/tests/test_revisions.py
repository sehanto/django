from contextlib import contextmanager
from datetime import date
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction
from django.urls import reverse

from apps.assets import services
from apps.assets.models import AssetRevision
from apps.core.exceptions import InvalidStateTransition, ValidationFailed
from apps.tenants.roles import Role

D = Decimal


@pytest.fixture
def depreciated(organization, make_asset):
    """4.800.000 over 48 months from January 2026; January to March posted."""
    asset = make_asset()
    services.post_depreciation(organization=organization, through=date(2026, 3, 31))
    return asset


def revise(asset, on=date(2026, 4, 1), months=60, residual="0", reason="Dipakai lebih lama"):
    return services.revise_estimate(asset=asset, effective_from=on, useful_life_months=months, residual_value=D(residual), reason=reason)


def test_a_revision_changes_depreciation_from_its_month_on(organization, depreciated, user):
    revision = services.revise_estimate(
        asset=depreciated, effective_from=date(2026, 4, 20), useful_life_months=60, residual_value=D("0"),
        reason="Dipakai lebih lama", created_by=user,
    )
    assert revision.effective_from == date(2026, 4, 1)
    assert (revision.previous_useful_life_months, revision.previous_residual_value) == (48, D("0"))
    depreciated.refresh_from_db()
    assert (depreciated.useful_life_months, depreciated.current_useful_life_months) == (48, 60)  # the original stays recorded

    services.post_depreciation(organization=organization, through=date(2026, 5, 31))
    amounts = [p.amount for p in depreciated.postings.order_by("year", "month")]
    assert amounts[:3] == [D("100000")] * 3
    assert set(amounts[3:]) <= {D("78947"), D("78948")}  # 4.500.000 over the 57 remaining months
    assert depreciated.schedule[-1].book_value == 0 and len(depreciated.schedule) == 60


def test_only_assets_with_posted_depreciation_are_revised(make_asset):
    with pytest.raises(InvalidStateTransition, match="edit it instead"):
        revise(make_asset())


@pytest.mark.parametrize("kwargs, message", [
    ({"on": date(2026, 2, 1)}, "from 04/2026"),  # February is posted
    ({"months": 3}, "at least 4 months"),
    ({"residual": "4600000"}, "more than the book value"),  # 4.500.000 left
    ({"reason": "  "}, "reason"),
])
def test_revisions_are_validated(depreciated, kwargs, message):
    with pytest.raises(ValidationFailed, match=message):
        revise(depreciated, **kwargs)


def test_a_revision_can_be_followed_by_another(organization, depreciated):
    revise(depreciated, months=60)
    revise(depreciated, months=36)  # same month, nothing posted in between: the later one wins
    assert depreciated.current_useful_life_months == 36 and len(depreciated.schedule) == 36
    with pytest.raises(ValidationFailed, match="from 04/2026"):
        revise(depreciated, on=date(2026, 3, 1))


@contextmanager
def refused():
    with pytest.raises(IntegrityError), transaction.atomic():
        yield


def test_revisions_are_append_only_in_the_database(organization, depreciated):
    revision = revise(depreciated)
    with refused():
        AssetRevision.objects.filter(pk=revision.pk).update(useful_life_months=12)
    with refused():
        revision.delete()
    for effective_from in (date(2026, 3, 1), date(2026, 5, 15)):  # posted month; not a month start
        with refused():
            AssetRevision.objects.create(
                organization=organization, asset=depreciated, effective_from=effective_from, useful_life_months=60,
                residual_value=D("0"), previous_useful_life_months=48, previous_residual_value=D("0"), reason="x",
            )


def test_revising_through_the_page_and_disclosing_it(auth_client, user, organization, depreciated, make_member, client):
    user.language = "en"
    user.save(update_fields=["language"])
    detail = auth_client.get(reverse("assets:detail", args=[depreciated.pk]))
    assert detail.context["revision_form"].initial["effective_from"] == date(2026, 4, 1)

    auth_client.post(reverse("assets:revise", args=[depreciated.pk]), {
        "effective_from": "2026-04-01", "useful_life_months": "60", "residual_value": "0", "reason": "Dipakai lebih lama",
    })
    assert depreciated.revisions.count() == 1
    notes = auth_client.get(reverse("reports:notes"), {"as_of": "2026-12-31"}).content.decode()
    assert "Changes in accounting estimates" in notes and "48 → 60" in notes and "Dipakai lebih lama" in notes

    client.force_login(make_member(Role.VIEWER))
    assert client.post(reverse("assets:revise", args=[depreciated.pk]), {}).status_code == 403
