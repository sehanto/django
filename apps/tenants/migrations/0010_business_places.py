"""Places of business (branches with their own NITKU) replace the organization's single
NITKU (0009): a NITKU set there becomes a place of business."""

import django.core.validators
import django.db.models.deletion
from django.db import migrations, models


def nitku_to_place(apps, schema_editor):
    Organization = apps.get_model("tenants", "Organization")
    BusinessPlace = apps.get_model("tenants", "BusinessPlace")
    for organization in Organization.objects.exclude(nitku=""):
        BusinessPlace.objects.create(
            organization=organization, name="Cabang", nitku=organization.nitku,
            address=organization.address, city=organization.city,
        )


class Migration(migrations.Migration):

    dependencies = [
        ('tenants', '0009_organization_nitku'),
    ]

    operations = [
        migrations.CreateModel(
            name='BusinessPlace',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='created')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='updated')),
                ('name', models.CharField(help_text='E.g. Cabang Sleman.', max_length=100, verbose_name='name')),
                ('nitku', models.CharField(help_text="22 digits: the organization's 16-digit NPWP followed by the 6 digits of this place, as registered in Coretax.", max_length=22, validators=[django.core.validators.RegexValidator('^\\d{22}$', 'A NITKU has 22 digits: the 16-digit NPWP followed by 6 digits.')], verbose_name='NITKU')),
                ('address', models.TextField(blank=True, max_length=500, verbose_name='address')),
                ('city', models.CharField(blank=True, max_length=100, verbose_name='city/regency')),
                ('is_active', models.BooleanField(default=True, verbose_name='active')),
                ('organization', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='+', to='tenants.organization', verbose_name='organization')),
            ],
            options={
                'verbose_name': 'place of business',
                'verbose_name_plural': 'places of business',
                'ordering': ['name'],
                'constraints': [models.UniqueConstraint(fields=('organization', 'nitku'), name='unique_business_place_nitku')],
            },
        ),
        migrations.RunPython(nitku_to_place, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name='organization',
            name='nitku',
        ),
    ]
