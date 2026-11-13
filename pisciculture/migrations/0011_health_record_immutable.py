from django.db import migrations


def install(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('CREATE TRIGGER aquatic_healthrecord_immutable BEFORE UPDATE OR DELETE ON pisciculture_healthrecord FOR EACH STATEMENT EXECUTE FUNCTION aquatic_reject_fact_change()')


def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER aquatic_healthrecord_immutable ON pisciculture_healthrecord')


class Migration(migrations.Migration):
    dependencies=[('pisciculture','0010_sanitaryproduct_healthrecord_and_more')]
    operations=[migrations.RunPython(install,uninstall)]
