from django.db import migrations
from django.db.models import F


def forwards(apps, schema_editor):
    # Invoices before proration had no credit: their coverage was worth the subtotal.
    apps.get_model("billing", "SubscriptionInvoice").objects.update(coverage_value=F("subtotal"))


class Migration(migrations.Migration):
    dependencies = [("billing", "0002_proration_credit")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
