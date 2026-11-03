from django.db import migrations

def protect(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('CREATE TRIGGER supply_immutable BEFORE UPDATE OR DELETE ON core_feeddistributionlink FOR EACH ROW EXECUTE FUNCTION elevage_supply_immutable();')
def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':schema_editor.execute('DROP TRIGGER supply_immutable ON core_feeddistributionlink;')
class Migration(migrations.Migration):
    dependencies=[('core','0044_supply_foundation')]
    operations=[migrations.RunPython(protect,unprotect)]
