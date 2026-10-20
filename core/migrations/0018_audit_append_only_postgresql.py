from django.db import migrations


def protect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('''
            CREATE FUNCTION elevage_reject_audit_mutation() RETURNS trigger AS $$
            BEGIN RAISE EXCEPTION 'Audit events are append-only'; END;
            $$ LANGUAGE plpgsql;
            CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON core_auditevent
            FOR EACH STATEMENT EXECUTE FUNCTION elevage_reject_audit_mutation();
        ''')


def unprotect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER audit_append_only ON core_auditevent; '
                              'DROP FUNCTION elevage_reject_audit_mutation();')


class Migration(migrations.Migration):
    dependencies = [('core', '0017_bootstrap_memberships')]
    operations = [migrations.RunPython(protect, unprotect)]
