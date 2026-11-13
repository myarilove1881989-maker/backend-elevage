from django.db import migrations

def install(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        for table in ('harvest','harvestmovement'):
            schema_editor.execute(f'CREATE TRIGGER aquatic_{table}_immutable BEFORE UPDATE OR DELETE ON pisciculture_{table} FOR EACH STATEMENT EXECUTE FUNCTION aquatic_reject_fact_change()')
def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        for table in ('harvest','harvestmovement'):schema_editor.execute(f'DROP TRIGGER aquatic_{table}_immutable ON pisciculture_{table}')
class Migration(migrations.Migration):
    dependencies=[('pisciculture','0014_harvest_harvestmovement_and_more')]
    operations=[migrations.RunPython(install,uninstall)]
