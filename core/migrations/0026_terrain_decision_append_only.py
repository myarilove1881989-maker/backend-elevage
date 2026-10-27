from django.db import migrations


def protect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''
            CREATE FUNCTION elevage_reject_decision_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Terrain decisions are immutable'; END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER terrain_decision_append_only BEFORE UPDATE OR DELETE ON core_terraindecision
            FOR EACH STATEMENT EXECUTE FUNCTION elevage_reject_decision_mutation();
        ''')


def unprotect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER terrain_decision_append_only ON core_terraindecision; '
            'DROP FUNCTION elevage_reject_decision_mutation();')


class Migration(migrations.Migration):
    dependencies = [('core', '0025_terrain_decisions')]
    operations = [migrations.RunPython(protect, unprotect)]
