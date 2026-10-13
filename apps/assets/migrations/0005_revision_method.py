"""Revisions also carry the depreciation method (a change in estimate too).

Revisions recorded before this kept their asset's method, so both columns are filled from
the asset. Revisions are append-only (0004), so the guard is lifted for this one update,
which runs as the schema owner like every migration.
"""

from django.db import migrations, models

METHODS = [("straight_line", "Straight line"), ("declining_balance", "Declining balance")]

BACKFILL_SQL = """
SET CONSTRAINTS ALL IMMEDIATE;
ALTER TABLE assets_assetrevision DISABLE TRIGGER assets_assetrevision_guard;
UPDATE assets_assetrevision r SET method = a.method, previous_method = a.method
  FROM assets_fixedasset a WHERE a.id = r.asset_id;
ALTER TABLE assets_assetrevision ENABLE TRIGGER assets_assetrevision_guard;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0004_immutable_revisions'),
    ]

    operations = [
        migrations.AddField(
            model_name='assetrevision', name='method',
            field=models.CharField(choices=METHODS, max_length=20, null=True, verbose_name='depreciation method'),
        ),
        migrations.AddField(
            model_name='assetrevision', name='previous_method',
            field=models.CharField(choices=METHODS, max_length=20, null=True, verbose_name='previous depreciation method'),
        ),
        migrations.RunSQL(BACKFILL_SQL, migrations.RunSQL.noop),
        migrations.AlterField(
            model_name='assetrevision', name='method',
            field=models.CharField(choices=METHODS, max_length=20, verbose_name='depreciation method'),
        ),
        migrations.AlterField(
            model_name='assetrevision', name='previous_method',
            field=models.CharField(choices=METHODS, max_length=20, verbose_name='previous depreciation method'),
        ),
    ]
