from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0013_vente_oeufs_compose_et_lien_mouvement'),
    ]

    operations = [
        migrations.AddField(
            model_name='mouvement',
            name='mort_nes',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='mouvement',
            name='note',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='mouvement',
            name='type_mouvement',
            field=models.CharField(
                choices=[
                    ('ACHAT', 'Achat'),
                    ('VENTE', 'Vente'),
                    ('MORTALITE', 'Mortalité'),
                    ('DON', 'Don'),
                    ('VOL', 'Vol'),
                    ('NAISSANCE', 'Naissance'),
                ],
                max_length=20,
            ),
        ),
    ]
