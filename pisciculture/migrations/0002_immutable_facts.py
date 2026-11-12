from django.db import migrations


def install(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute("""CREATE FUNCTION aquatic_reject_fact_change() RETURNS trigger
        LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Aquatic facts are append-only'; END $$""")
    for name in ('command','event','countline'):
        table='pisciculture_'+name
        schema_editor.execute(f'CREATE TRIGGER aquatic_{name}_immutable BEFORE UPDATE OR DELETE ON {table} '
            'FOR EACH STATEMENT EXECUTE FUNCTION aquatic_reject_fact_change()')


def uninstall(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for name in ('command','event','countline'):
        schema_editor.execute(f'DROP TRIGGER aquatic_{name}_immutable ON pisciculture_{name}')
    schema_editor.execute('DROP FUNCTION aquatic_reject_fact_change()')


class Migration(migrations.Migration):
    dependencies=[('pisciculture','0001_initial')]
    operations=[migrations.RunPython(install,uninstall)]
