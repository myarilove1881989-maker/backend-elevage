from django.db import migrations

def protect(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        for table in ['core_healthcommand','core_vaccinationtasklink']:
            schema_editor.execute(f'CREATE TRIGGER health_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION elevage_supply_immutable();')

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        for table in ['core_healthcommand','core_vaccinationtasklink']:
            schema_editor.execute(f'DROP TRIGGER health_immutable ON {table};')

class Migration(migrations.Migration):
    dependencies=[('core','0048_healthtask_vaccinationtasklink_healthcommand')]
    operations=[migrations.RunPython(protect,unprotect)]
