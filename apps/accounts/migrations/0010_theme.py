from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0009_pending_email"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="theme",
            field=models.CharField(
                choices=[
                    ("system", "Device setting"),
                    ("light", "Light"),
                    ("dark", "Dark"),
                ],
                default="system",
                max_length=10,
                verbose_name="appearance",
            ),
        ),
    ]
