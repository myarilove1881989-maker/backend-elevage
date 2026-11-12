from django.db import migrations

def install(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql': return
    for name in ('observation','observationrule'):
        schema_editor.execute(f'CREATE TRIGGER aquatic_{name}_immutable BEFORE UPDATE OR DELETE ON pisciculture_{name} FOR EACH STATEMENT EXECUTE FUNCTION aquatic_reject_fact_change()')

def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql': return
    for name in ('observation','observationrule'):
        schema_editor.execute(f'DROP TRIGGER aquatic_{name}_immutable ON pisciculture_{name}')

class Migration(migrations.Migration):
    dependencies=[('pisciculture','0003_observation_observationrule')]
    operations=[migrations.RunPython(install,uninstall)]
