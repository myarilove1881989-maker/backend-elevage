from django.db import migrations

def install(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':return
    for name in ('feedpurchase','feeddistribution'):
        schema_editor.execute(f'CREATE TRIGGER aquatic_{name}_immutable BEFORE UPDATE OR DELETE ON pisciculture_{name} FOR EACH STATEMENT EXECUTE FUNCTION aquatic_reject_fact_change()')
    schema_editor.execute("""CREATE FUNCTION aquatic_common_feed_only() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.species_id IS NULL AND NOT EXISTS (SELECT 1 FROM core_supplyproduct WHERE id=NEW.product_id AND kind='FEED') THEN
            RAISE EXCEPTION 'Common pool is for feed only';
          END IF;
          RETURN NEW;
        END $$""")
    schema_editor.execute('CREATE TRIGGER aquatic_common_feed_insert BEFORE INSERT ON core_supplymovement FOR EACH ROW EXECUTE FUNCTION aquatic_common_feed_only()')

def uninstall(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':return
    schema_editor.execute('DROP TRIGGER aquatic_common_feed_insert ON core_supplymovement')
    schema_editor.execute('DROP FUNCTION aquatic_common_feed_only()')
    for name in ('feedpurchase','feeddistribution'):
        schema_editor.execute(f'DROP TRIGGER aquatic_{name}_immutable ON pisciculture_{name}')

class Migration(migrations.Migration):
    dependencies=[('pisciculture','0008_feedpurchase_feeddistribution')]
    operations=[migrations.RunPython(install,uninstall)]
