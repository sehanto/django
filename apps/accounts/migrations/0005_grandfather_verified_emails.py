from django.db import migrations
from django.db.models import F


def forwards(apps, schema_editor):
    # Accounts that existed before verification was introduced are trusted as-is.
    User = apps.get_model("accounts", "User")
    User.objects.filter(email_verified_at__isnull=True).update(email_verified_at=F("date_joined"))


class Migration(migrations.Migration):
    dependencies = [("accounts", "0004_user_email_verified_at")]

    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
