from django.db import migrations


def protect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''
            CREATE FUNCTION elevage_reject_terrain_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Terrain declarations are immutable'; END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER terrain_declaration_append_only BEFORE UPDATE OR DELETE ON core_terrainsubmission
            FOR EACH STATEMENT EXECUTE FUNCTION elevage_reject_terrain_mutation();
        ''')


def unprotect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER terrain_declaration_append_only ON core_terrainsubmission; '
                              'DROP FUNCTION elevage_reject_terrain_mutation();')


class Migration(migrations.Migration):
    dependencies = [('core', '0019_devicetransportchallenge_terrainsubmission_and_more')]
    operations = [migrations.RunPython(protect, unprotect)]
