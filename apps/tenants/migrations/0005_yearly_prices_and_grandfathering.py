from datetime import timedelta

from django.db import migrations
from django.utils import timezone

GRANDFATHER_DAYS = 30


def forwards(apps, schema_editor):
    Plan = apps.get_model("tenants", "Plan")
    Organization = apps.get_model("tenants", "Organization")
    for plan in Plan.objects.all():
        if not plan.yearly_price:
            plan.yearly_price = plan.monthly_price * 10  # annual = 10 months (2 months free)
            plan.save(update_fields=["yearly_price"])
    # Organizations already on a paid plan keep it for a grace month before billing applies.
    paid_until = timezone.localdate() + timedelta(days=GRANDFATHER_DAYS)
    Organization.objects.filter(plan__monthly_price__gt=0, plan_paid_until__isnull=True).update(plan_paid_until=paid_until)


class Migration(migrations.Migration):
    dependencies = [("tenants", "0004_subscription_billing_fields")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
