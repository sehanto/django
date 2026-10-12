from django.db import migrations

PLANS = [
    # code, name, price, max_users, max_invoices_per_month, is_default, sort
    ("gratis", "Gratis", 0, 1, 30, True, 1),
    ("usaha", "Usaha", 99000, 3, 300, False, 2),
    ("pro", "Pro", 249000, 10, None, False, 3),
]


def seed_plans(apps, schema_editor):
    Plan = apps.get_model("tenants", "Plan")
    for code, name, price, users, invoices, default, sort in PLANS:
        Plan.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "monthly_price": price,
                "max_users": users,
                "max_invoices_per_month": invoices,
                "is_default": default,
                "sort_order": sort,
            },
        )


class Migration(migrations.Migration):
    dependencies = [("tenants", "0001_initial")]

    operations = [migrations.RunPython(seed_plans, migrations.RunPython.noop)]
