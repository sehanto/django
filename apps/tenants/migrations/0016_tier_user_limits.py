"""Users per tier: Starter 1, Growth 3, Pro 5. Members already in an organization stay; the limit
applies to adding more (``tenants.services`` refuses a member over it) and to switching plans."""

from django.db import migrations

USERS = {"starter": 1, "growth": 3, "pro": 5}
EARLIER = {"starter": 3, "growth": 10, "pro": None}


def set_limits(apps, limits):
    Plan = apps.get_model("tenants", "Plan")
    for code, users in limits.items():
        Plan.objects.filter(code=code).update(max_users=users)


class Migration(migrations.Migration):

    dependencies = [
        ("tenants", "0015_plan_tiers_and_trial"),
    ]

    operations = [
        migrations.RunPython(lambda apps, editor: set_limits(apps, USERS), lambda apps, editor: set_limits(apps, EARLIER)),
    ]
