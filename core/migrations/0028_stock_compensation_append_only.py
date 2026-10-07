from django.db import migrations


def protect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''
            CREATE FUNCTION elevage_reject_stock_compensation_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Stock compensations are immutable'; END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER terrain_stock_compensation_append_only BEFORE UPDATE OR DELETE ON core_terrainstockadjustment
            FOR EACH STATEMENT EXECUTE FUNCTION elevage_reject_stock_compensation_mutation();
        ''')


def unprotect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER terrain_stock_compensation_append_only ON core_terrainstockadjustment; '
            'DROP FUNCTION elevage_reject_stock_compensation_mutation();')


class Migration(migrations.Migration):
    dependencies = [('core', '0027_terrain_reversal_compensations')]
    operations = [migrations.RunPython(protect, unprotect)]
