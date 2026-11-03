from django.db import migrations


TABLES = ('supplyoperation', 'supplyreceipt', 'supplyvial', 'supplyvialopening', 'supplymovement')


def protect(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute("""CREATE FUNCTION elevage_supply_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'SUPPLY_IMMUTABLE'; END;
        $$ LANGUAGE plpgsql;""")
    for table in TABLES:
        schema_editor.execute(f'''CREATE TRIGGER supply_immutable BEFORE UPDATE OR DELETE
            ON core_{table} FOR EACH ROW EXECUTE FUNCTION elevage_supply_immutable();''')


def unprotect(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    for table in TABLES:
        schema_editor.execute(f'DROP TRIGGER supply_immutable ON core_{table};')
    schema_editor.execute('DROP FUNCTION elevage_supply_immutable();')


class Migration(migrations.Migration):
    dependencies = [('core', '0039_supply_foundation')]
    operations = [migrations.RunPython(protect, unprotect)]
