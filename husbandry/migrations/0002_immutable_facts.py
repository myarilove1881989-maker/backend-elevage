from django.db import migrations

def protect(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':return
    schema_editor.execute("CREATE FUNCTION husbandry_protect_fact() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'HISTORY_IMMUTABLE'; END $$")
    for name in ['command','parameterrule','technicalreading','reproductionevent']:
        schema_editor.execute('CREATE TRIGGER husbandry_'+name+'_immutable BEFORE UPDATE OR DELETE ON husbandry_'+name+' FOR EACH ROW EXECUTE FUNCTION husbandry_protect_fact()')

def unprotect(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':return
    for name in ['command','parameterrule','technicalreading','reproductionevent']:
        schema_editor.execute('DROP TRIGGER husbandry_'+name+'_immutable ON husbandry_'+name)
    schema_editor.execute('DROP FUNCTION husbandry_protect_fact()')
class Migration(migrations.Migration):
    dependencies=[('husbandry','0001_initial')]
    operations=[migrations.RunPython(protect,unprotect)]
