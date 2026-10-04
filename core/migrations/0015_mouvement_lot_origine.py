from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0014_mouvement_naissance'),
    ]

    operations = [
        migrations.AddField(
            model_name='mouvement',
            name='lot_origine',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                related_name='naissances_issues', to='core.lot',
            ),
        ),
    ]
