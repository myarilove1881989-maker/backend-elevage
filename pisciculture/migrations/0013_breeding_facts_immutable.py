from django.db import migrations


def install(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('CREATE TRIGGER aquatic_breedingfact_immutable BEFORE UPDATE OR DELETE ON pisciculture_breedingfact FOR EACH STATEMENT EXECUTE FUNCTION aquatic_reject_fact_change()')


def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER aquatic_breedingfact_immutable ON pisciculture_breedingfact')


class Migration(migrations.Migration):
    dependencies=[('pisciculture','0012_broodgroup_reproduction_breedingfact_and_more')]
    operations=[migrations.RunPython(install,uninstall)]
