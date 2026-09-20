from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("core", "0006_seed_species_catalog")]

    operations = [
        migrations.AddField(
            model_name="client",
            name="pays",
            field=models.CharField(blank=True, default="", max_length=2),
        ),
        migrations.AddField(
            model_name="client",
            name="ville",
            field=models.CharField(blank=True, default="", max_length=100),
        ),
    ]
