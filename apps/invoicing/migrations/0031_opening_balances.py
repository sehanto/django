from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("invoicing", "0030_document_number_validators"),
    ]

    operations = [
        migrations.AddField(
            model_name="invoice",
            name="opening_balance",
            field=models.BooleanField(
                default=False, editable=False, verbose_name="opening balance"
            ),
        ),
    ]
