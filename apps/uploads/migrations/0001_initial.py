import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("tenants", "0015_plan_tiers_and_trial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="Upload",
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
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("contacts", "Contacts"),
                            ("items", "Items"),
                            ("accounts", "Accounts"),
                            ("opening_balances", "Opening balances"),
                            ("fixed_assets", "Fixed assets brought over"),
                            ("output_fakturs", "Faktur Pajak numbers (Coretax, sales)"),
                            ("input_fakturs", "Input VAT fakturs (Coretax, purchases)"),
                            ("withholding_slips", "Bukti potong numbers (Coretax)"),
                            ("bank_statement", "Bank statement"),
                        ],
                        max_length=30,
                        verbose_name="type",
                    ),
                ),
                ("file_name", models.CharField(max_length=255, verbose_name="file")),
                ("sha256", models.CharField(max_length=64, verbose_name="SHA-256")),
                ("rows", models.PositiveIntegerField(verbose_name="rows")),
                ("applied", models.PositiveIntegerField(verbose_name="rows applied")),
                ("skipped", models.PositiveIntegerField(verbose_name="rows skipped")),
                (
                    "summary",
                    models.CharField(
                        blank=True, max_length=500, verbose_name="summary"
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to=settings.AUTH_USER_MODEL,
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
            ],
            options={
                "verbose_name": "file upload",
                "verbose_name_plural": "file uploads",
                "ordering": ["-created_at", "-id"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("organization", "kind", "sha256"),
                        name="upload_applied_once",
                    )
                ],
            },
        ),
    ]
