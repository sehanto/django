"""SAK EMKM 11.14 (12.13 for intangibles): fixed assets are depreciated without a residual value."""

from decimal import Decimal

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("assets", "0007_acquisition_date_help"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="fixedasset",
            name="residual_below_cost",
        ),
        migrations.AlterField(
            model_name="assetrevision",
            name="previous_residual_value",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                editable=False,
                max_digits=18,
                verbose_name="previous residual value",
            ),
        ),
        migrations.AlterField(
            model_name="assetrevision",
            name="residual_value",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                editable=False,
                max_digits=18,
                verbose_name="residual value",
            ),
        ),
        migrations.AlterField(
            model_name="fixedasset",
            name="residual_value",
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal("0.00"),
                editable=False,
                max_digits=18,
                verbose_name="residual value",
            ),
        ),
        migrations.AddConstraint(
            model_name="assetrevision",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("previous_residual_value", 0), ("residual_value", 0)
                ),
                name="revision_without_residual_value",
            ),
        ),
        migrations.AddConstraint(
            model_name="fixedasset",
            constraint=models.CheckConstraint(
                condition=models.Q(("residual_value", 0)),
                name="asset_without_residual_value",
            ),
        ),
    ]
