from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("dibuat", auto_now_add=True)
    updated_at = models.DateTimeField("diperbarui", auto_now=True)

    class Meta:
        abstract = True


class TenantQuerySet(models.QuerySet):
    def for_organization(self, organization):
        return self.filter(organization=organization)


class TenantOwnedModel(TimeStampedModel):
    """Base for every record that belongs to exactly one tenant.

    Data isolation is enforced by always querying through
    ``Model.objects.for_organization(org)``; views never query the raw manager.
    """

    organization = models.ForeignKey(
        "tenants.Organization",
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name="organisasi",
    )

    objects = TenantQuerySet.as_manager()

    class Meta:
        abstract = True


def money_field(verbose_name: str, **kwargs) -> models.DecimalField:
    """Uniform definition for every monetary column."""
    from .money import MONEY_DECIMAL_PLACES, MONEY_MAX_DIGITS, ZERO

    kwargs.setdefault("default", ZERO)
    return models.DecimalField(
        verbose_name,
        max_digits=MONEY_MAX_DIGITS,
        decimal_places=MONEY_DECIMAL_PLACES,
        **kwargs,
    )


class DocumentSequence(TenantOwnedModel):
    """Monotonic counter per organization, document prefix and month."""

    prefix = models.CharField(max_length=10)
    year = models.PositiveSmallIntegerField()
    month = models.PositiveSmallIntegerField()
    last_number = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["organization", "prefix", "year", "month"], name="unique_document_sequence"
            )
        ]
