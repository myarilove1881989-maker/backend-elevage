from django.db import migrations

def install(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('CREATE TRIGGER aquatic_stockfact_immutable BEFORE UPDATE OR DELETE ON pisciculture_stockfact FOR EACH STATEMENT EXECUTE FUNCTION aquatic_reject_fact_change()')

def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER aquatic_stockfact_immutable ON pisciculture_stockfact')

class Migration(migrations.Migration):
    dependencies=[('pisciculture','0006_stockfact')]
    operations=[migrations.RunPython(install,uninstall)]
