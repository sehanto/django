import django.db.models.deletion
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("ledger", "0021_opening_balances"),
        ("tenants", "0015_plan_tiers_and_trial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="StatementLine",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "created_at",
                    models.DateTimeField(auto_now_add=True, verbose_name="created"),
                ),
                (
                    "updated_at",
                    models.DateTimeField(auto_now=True, verbose_name="updated"),
                ),
                ("date", models.DateField(verbose_name="date")),
                (
                    "description",
                    models.CharField(max_length=255, verbose_name="description"),
                ),
                (
                    "reference",
                    models.CharField(
                        blank=True, max_length=100, verbose_name="reference"
                    ),
                ),
                (
                    "amount",
                    models.DecimalField(
                        decimal_places=2,
                        default=Decimal("0.00"),
                        max_digits=18,
                        verbose_name="amount",
                    ),
                ),
                (
                    "balance",
                    models.DecimalField(
                        blank=True,
                        decimal_places=2,
                        max_digits=18,
                        null=True,
                        verbose_name="balance",
                    ),
                ),
                (
                    "key",
                    models.CharField(
                        editable=False, max_length=64, verbose_name="line key"
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("unmatched", "Not reconciled"),
                            ("matched", "Reconciled"),
                            ("ignored", "Ignored"),
                        ],
                        default="unmatched",
                        max_length=10,
                        verbose_name="status",
                    ),
                ),
                (
                    "reconciled_at",
                    models.DateTimeField(
                        blank=True, null=True, verbose_name="reconciled at"
                    ),
                ),
                (
                    "account",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="ledger.account",
                        verbose_name="bank account",
                    ),
                ),
                (
                    "journal_line",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="statement_line",
                        to="ledger.journalline",
                        verbose_name="journal line",
                    ),
                ),
                (
                    "organization",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="+",
                        to="tenants.organization",
                        verbose_name="organization",
                    ),
                ),
                (
                    "reconciled_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "verbose_name": "bank statement line",
                "verbose_name_plural": "bank statement lines",
                "ordering": ["date", "id"],
                "indexes": [
                    models.Index(
                        fields=["organization", "account", "status", "date"],
                        name="banking_sta_organiz_997039_idx",
                    )
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "key"), name="statement_line_once"
                    ),
                    models.CheckConstraint(
                        condition=models.Q(("amount", 0), _negated=True),
                        name="statement_line_has_amount",
                    ),
                    models.CheckConstraint(
                        condition=models.Q(
                            models.Q(
                                ("journal_line__isnull", False), ("status", "matched")
                            ),
                            models.Q(
                                models.Q(("status", "matched"), _negated=True),
                                ("journal_line__isnull", True),
                            ),
                            _connector="OR",
                        ),
                        name="statement_line_matched_with_journal_line",
                    ),
                ],
            },
        ),
    ]
