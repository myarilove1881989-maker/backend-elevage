from django.db import migrations


def install(apps,schema_editor):
    if schema_editor.connection.vendor!='postgresql':
        return
    schema_editor.execute('''
        CREATE FUNCTION elevage_cash_origin_immutable() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Physical cash receipts cannot be deleted' USING ERRCODE='P0001';
            END IF;
            IF ROW(NEW.exploitation_id,NEW.submission_id,NEW.client_id,NEW.created_by_id,NEW.payment_id,
                   NEW.business_occurred_at,NEW.montant_recu,NEW.mode,NEW.note)
               IS DISTINCT FROM
               ROW(OLD.exploitation_id,OLD.submission_id,OLD.client_id,OLD.created_by_id,OLD.payment_id,
                   OLD.business_occurred_at,OLD.montant_recu,OLD.mode,OLD.note) THEN
                RAISE EXCEPTION 'Physical cash receipt origin cannot be changed' USING ERRCODE='P0001';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
        CREATE TRIGGER cash_origin_immutable BEFORE UPDATE OR DELETE ON core_encaissementterrain
        FOR EACH ROW EXECUTE FUNCTION elevage_cash_origin_immutable();
    ''')


def remove(apps,schema_editor):
    if schema_editor.connection.vendor=='postgresql':
        schema_editor.execute('DROP TRIGGER IF EXISTS cash_origin_immutable ON core_encaissementterrain; '
            'DROP FUNCTION IF EXISTS elevage_cash_origin_immutable();')


class Migration(migrations.Migration):
    dependencies=[('core','0023_alter_lettrage_montant_alter_payment_montant_and_more')]
    operations=[migrations.RunPython(install,remove)]
