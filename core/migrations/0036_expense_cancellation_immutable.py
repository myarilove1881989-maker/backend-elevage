from django.db import migrations


def protect(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute("""
        CREATE FUNCTION elevage_expense_cancellation_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'CANCELLATION_IMMUTABLE'; END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER expense_cancellation_immutable
        BEFORE UPDATE OR DELETE ON core_expensecancellation
        FOR EACH ROW EXECUTE FUNCTION elevage_expense_cancellation_immutable();
    """)


def unprotect(apps, schema_editor):
    if schema_editor.connection.vendor == 'postgresql':
        schema_editor.execute('DROP TRIGGER expense_cancellation_immutable ON core_expensecancellation;')
        schema_editor.execute('DROP FUNCTION elevage_expense_cancellation_immutable();')


class Migration(migrations.Migration):
    dependencies = [('core', '0035_task_cancellation_reason_task_cancelled_at_and_more')]
    operations = [migrations.RunPython(protect, unprotect)]
