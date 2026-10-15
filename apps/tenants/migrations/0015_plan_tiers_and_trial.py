"""Starter → Growth → Pro, and a trial instead of the free plan.

Usaha becomes Starter and Pro becomes Growth, at their prices and limits (no subscriber pays
more); Pro is a new tier. Starter is the default plan, which a new organization tries for
``BILLING_TRIAL_DAYS``. Organizations on the free plan start that trial now; the free plan is
removed once nothing refers to it."""

from datetime import timedelta

from django.conf import settings
from django.utils import timezone


import django.contrib.postgres.fields
from django.db import migrations, models


TIERS = [  # code, earlier code, name, monthly, yearly (10 months), users, invoices/month, features, order
    ("growth", "pro", "Growth", 249000, 2490000, 10, None, ["payroll", "api"], 2),  # first: frees the code "pro"
    ("starter", "usaha", "Starter", 99000, 990000, 3, 300, [], 1),
    ("pro", None, "Pro", 499000, 4990000, None, None, ["payroll", "api", "coretax_sending", "fixed_assets"], 3),
]


def tiers(apps, schema_editor):
    Plan = apps.get_model("tenants", "Plan")
    Organization = apps.get_model("tenants", "Organization")
    SubscriptionInvoice = apps.get_model("billing", "SubscriptionInvoice")
    Plan.objects.update(is_default=False)
    for code, earlier, name, monthly, yearly, users, invoices, features, order in TIERS:
        plan = Plan.objects.filter(code=earlier).first() if earlier else None
        plan = plan or Plan.objects.filter(code=code).first() or Plan(code=code)
        plan.code, plan.name, plan.monthly_price, plan.yearly_price = code, name, monthly, yearly
        plan.max_users, plan.max_invoices_per_month, plan.features, plan.sort_order = users, invoices, features, order
        plan.is_default = code == "starter"
        plan.save()
    starter = Plan.objects.get(code="starter")
    trial_end = timezone.localdate() + timedelta(days=settings.BILLING_TRIAL_DAYS)
    Organization.objects.filter(plan__monthly_price__lte=0).update(plan=starter, plan_paid_until=trial_end, trial_ends_on=trial_end)
    for free in Plan.objects.filter(monthly_price__lte=0):
        if not Organization.objects.filter(plan=free).exists() and not SubscriptionInvoice.objects.filter(plan=free).exists():
            free.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0014_pph_final_since"),
        ("billing", "0010_seller_snapshot"),
    ]

    operations = [
        migrations.AddField(
            model_name="organization",
            name="trial_ends_on",
            field=models.DateField(
                blank=True, editable=False, null=True, verbose_name="trial ends on"
            ),
        ),
        migrations.AddField(
            model_name="plan",
            name="features",
            field=django.contrib.postgres.fields.ArrayField(
                base_field=models.CharField(
                    choices=[
                        ("payroll", "Payroll and PPh 21"),
                        ("api", "REST API"),
                        ("coretax_sending", "Sending to Coretax"),
                        ("fixed_assets", "Fixed asset register"),
                    ],
                    max_length=20,
                ),
                blank=True,
                default=list,
                help_text="What the plan includes beyond the bookkeeping every plan has.",
                size=None,
                verbose_name="features",
            ),
        ),
        migrations.AlterField(
            model_name="plan",
            name="is_default",
            field=models.BooleanField(
                default=False,
                help_text="New organizations start on a trial of it.",
                verbose_name="default plan",
            ),
        ),
        migrations.AlterField(
            model_name="organization",
            name="plan_paid_until",
            field=models.DateField(
                blank=True, null=True, verbose_name="plan paid until",
                help_text="Last day covered by payment or by the trial; afterwards (and its grace days) the organization is read-only.",
            ),
        ),
        migrations.RunPython(tiers, migrations.RunPython.noop),
    ]
