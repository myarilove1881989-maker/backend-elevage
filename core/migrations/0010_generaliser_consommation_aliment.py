from django.db import migrations, models
from django.utils import timezone


class Migration(migrations.Migration):
    dependencies = [("core", "0009_mouvementoeufs_vente_oeufs")]

    operations = [
        migrations.AddField(
            model_name="consommationaliment",
            name="aliment",
            field=models.CharField(default="Aliment", max_length=120),
        ),
        # L'heure des anciens enregistrements est inconnue. Ils restent NULL.
        migrations.AddField(
            model_name="consommationaliment",
            name="distribution_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="consommationaliment",
            name="distribution_at",
            field=models.DateTimeField(blank=True, default=timezone.now, null=True),
        ),
        migrations.AlterModelOptions(
            name="consommationaliment",
            options={"ordering": ("-date", "-distribution_at", "-id")},
        ),
    ]
