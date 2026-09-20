from django.db import migrations


SPECIES_CATALOG = (
    "Poulet",
    "Dinde",
    "Canard",
    "Pintade",
    "Porc",
    "Bovin",
    "Mouton",
    "Chèvre",
    "Lapin",
    "Poisson",
    "Œufs",
)


def seed_species_catalog(apps, schema_editor):
    Exploitation = apps.get_model("core", "Exploitation")
    Espece = apps.get_model("core", "Espece")

    for exploitation in Exploitation.objects.all().iterator():
        existing = {
            name.casefold()
            for name in Espece.objects.filter(exploitation=exploitation)
            .values_list("nom", flat=True)
        }
        Espece.objects.bulk_create(
            [
                Espece(nom=name, exploitation=exploitation)
                for name in SPECIES_CATALOG
                if name.casefold() not in existing
            ]
        )


class Migration(migrations.Migration):
    dependencies = [("core", "0005_passwordresetcode")]

    operations = [migrations.RunPython(seed_species_catalog, migrations.RunPython.noop)]
