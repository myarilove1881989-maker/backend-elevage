"""CI-only PostgreSQL18.4 upgrade and restore of a wholly synthetic corpus."""
import hashlib
import importlib
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import threading
import time
from decimal import Decimal
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
url = urlsplit(os.environ.get('DATABASE_URL', ''))
assert os.environ.get('GITHUB_ACTIONS') == 'true'
assert url.hostname == '127.0.0.1' and url.path == '/phase2k_source_test'
assert url.username == 'phase2k_test' and url.port == 55439
os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings'
import django
django.setup()
from django.core.management import call_command
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
import psycopg2

PROOF = ROOT/'phase2k-evidence'
PROOF.mkdir(exist_ok=True)

def digest(rows):
    return hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()

def amounts(model):
    return [(pk,str(Decimal(str(value)).quantize(Decimal('.01')))) for pk,value in model.objects.order_by('pk').values_list('pk','montant')]

def main():
    with connection.cursor() as cursor:
        cursor.execute('SHOW server_version')
        version = cursor.fetchone()[0]
    assert version.split()[0] == '18.4', version
    call_command('migrate','core','0015',interactive=False)
    executor = MigrationExecutor(connection)
    apps = executor.loader.project_state([('core','0015_mouvement_lot_origine')]).apps
    User,Farm,Client,Payment = [apps.get_model('core',name) for name in ('User','Exploitation','Client','Payment')]
    Lot,Vente,Lettrage,Espece = [apps.get_model('core',name) for name in ('Lot','Vente','Lettrage','Espece')]
    for i in range(20):
        owner=User.objects.create(username=f'synthetic_owner_{i}',password='!')
        farm=Farm.objects.create(nom=f'Synthetic farm {i}',proprietaire=owner)
        owner.exploitation=farm;owner.save()
        for j in range(2): User.objects.create(username=f'synthetic_operator_{i}_{j}',password='!',exploitation=farm)
        client=Client.objects.create(nom=f'Synthetic client {i}',exploitation=farm)
        Payment.objects.bulk_create([Payment(client=client,exploitation=farm,montant=123.25+j,date='2026-10-08') for j in range(100)])
        species=Espece.objects.create(exploitation=farm,nom=f'Synthetic species {i}')
        lot=Lot.objects.create(exploitation=farm,espece=species,nom=f'Synthetic lot {i}',date_debut='2026-10-08')
        sale=Vente.objects.create(lot=lot,client=client,quantite=100,prix_unitaire=Decimal('100.00'),montant_total=Decimal('10000.00'))
        Lettrage.objects.create(vente=sale,payment=Payment.objects.filter(exploitation=farm).first(),montant=123.25)
    before=amounts(Payment)
    allocations_before=amounts(Lettrage)
    validator=importlib.import_module('core.migrations.0023_alter_lettrage_montant_alter_payment_montant_and_more').validate_existing_money
    rejected=[]
    original=Payment.objects.first()
    for label,value in [('subcent',123.251),('negative',-.01),('overflow',10000000000.0),('nan',float('nan')),('infinity',float('inf'))]:
        Payment.objects.filter(pk=original.pk).update(montant=value)
        try:
            with connection.schema_editor() as editor:validator(apps,editor)
        except RuntimeError as error:
            assert 'no conversion performed' in str(error)
            rejected.append(label)
        else:raise AssertionError(f'Invalid legacy money accepted: {label}')
        finally:Payment.objects.filter(pk=original.pk).update(montant=original.montant)
    durations={};started={};locks=set();monitor_errors=[];stop=threading.Event()
    def sample_locks():
        monitor=psycopg2.connect(os.environ['DATABASE_URL']);monitor.autocommit=True
        try:
            while not stop.wait(.01):
                with monitor.cursor() as cursor:
                    cursor.execute("SELECT mode,granted FROM pg_locks WHERE pid=%s AND relation IS NOT NULL",[connection_pid])
                    locks.update(cursor.fetchall())
        except BaseException as error:monitor_errors.append(type(error).__name__)
        finally: monitor.close()
    with connection.cursor() as cursor:
        cursor.execute('SELECT pg_backend_pid()');connection_pid=cursor.fetchone()[0]
    def progress(action,migration=None,fake=False):
        if action=='apply_start':started[str(migration)]=time.monotonic()
        if action=='apply_success':durations[str(migration)]=round(time.monotonic()-started[str(migration)],6)
    thread=threading.Thread(target=sample_locks);thread.start()
    try:
        executor=MigrationExecutor(connection,progress)
        executor.migrate(executor.loader.graph.leaf_nodes())
    finally:stop.set();thread.join()
    assert not monitor_errors and locks, 'Migration lock sampling failed'
    from core.models import Payment,Lettrage,ExploitationMembership,Exploitation,AuditEvent
    assert before==amounts(Payment)
    assert allocations_before==amounts(Lettrage)
    assert ExploitationMembership.objects.filter(role='OWNER').count()==20
    assert ExploitationMembership.objects.filter(role='OPERATEUR').count()==40
    assert not Exploitation.objects.filter(offline_policy_enabled=True).exists()
    for i in range(10):
        AuditEvent.objects.create(exploitation_id=1,actor_user_id=1,action='SYNTHETIC_REHEARSAL',entity_type='core.payment',entity_id=str(i+1),after_data={'synthetic':True})
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('PG')}
    env.update(PGHOST='127.0.0.1',PGPORT='55439',PGUSER='phase2k_test',PGPASSWORD=url.password,PGDATABASE='phase2k_source_test')
    archive=PROOF/'synthetic.dump'
    container=os.environ['PHASE2K_POSTGRES_CONTAINER']
    assert container and all(character in '0123456789abcdef' for character in container)
    # Use the service image's matching 18.4 clients, without installing a server.
    with archive.open('wb') as output:
        subprocess.run(['docker','exec',container,'pg_dump','-U','phase2k_test',
            '-d','phase2k_source_test','-Fc','--no-owner','--no-acl'],stdout=output,check=True)
    connection.ensure_connection()
    admin=psycopg2.connect(os.environ['DATABASE_URL']);admin.autocommit=True
    with admin.cursor() as cursor:cursor.execute('CREATE DATABASE phase2k_restore_test')
    admin.close()
    env['PGDATABASE']='phase2k_restore_test'
    with archive.open('rb') as input_file:
        subprocess.run(['docker','exec','-i',container,'pg_restore','-U','phase2k_test',
            '--exit-on-error','--single-transaction','--no-owner','--no-acl',
            '-d','phase2k_restore_test'],stdin=input_file,check=True)
    restored=psycopg2.connect(host='127.0.0.1',port=55439,user='phase2k_test',password=url.password,dbname='phase2k_restore_test');restored.autocommit=True
    checks={}
    queries={
        'payments': 'SELECT id,montant::text FROM core_payment ORDER BY id',
        'allocations': 'SELECT id,vente_id,payment_id,montant::text FROM core_lettrage ORDER BY id',
        'memberships': 'SELECT user_id,exploitation_id,role FROM core_exploitationmembership ORDER BY user_id',
        'audit': 'SELECT id,exploitation_id,actor_user_id,action,entity_type,entity_id,after_data::text FROM core_auditevent ORDER BY id',
        'migrations': 'SELECT app,name FROM django_migrations ORDER BY app,name',
        'constraints': "SELECT conrelid::regclass::text,conname,contype,convalidated,ARRAY(SELECT attname FROM unnest(conkey) WITH ORDINALITY AS k(num,pos) JOIN pg_attribute a ON a.attrelid=conrelid AND a.attnum=k.num ORDER BY k.pos),ARRAY(SELECT attname FROM unnest(confkey) WITH ORDINALITY AS k(num,pos) JOIN pg_attribute a ON a.attrelid=confrelid AND a.attnum=k.num ORDER BY k.pos),confrelid::regclass::text FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY conrelid::regclass::text,conname",
        'triggers': 'SELECT tgname,pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgname',
        'functions': "SELECT proname,pg_get_functiondef(oid) FROM pg_proc WHERE pronamespace='public'::regnamespace ORDER BY proname",
        'sequences': "SELECT sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename",
    }
    for name,query in queries.items():
        with connection.cursor() as cursor:cursor.execute(query);source=cursor.fetchall()
        with restored.cursor() as cursor:cursor.execute(query);target=cursor.fetchall()
        assert source==target,name
        checks[name]=digest(source)
    # Compare every table's synthetic rows, without writing their contents to logs.
    with connection.cursor() as cursor:
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables=[row[0] for row in cursor.fetchall()]
    for table in tables:
        assert re.fullmatch('[a-z_0-9]+',table)
        query=f'SELECT row_to_json(t)::text FROM {table} t ORDER BY 1'
        with connection.cursor() as cursor:cursor.execute(query);source=cursor.fetchall()
        with restored.cursor() as cursor:cursor.execute(query);target=cursor.fetchall()
        assert source==target,table
        checks[f'table:{table}']=digest(source)
    definitions="SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY conname,pg_get_constraintdef(oid)"
    with connection.cursor() as cursor:cursor.execute(definitions);source=cursor.fetchall()
    with restored.cursor() as cursor:cursor.execute(definitions);target=cursor.fetchall()
    # PostgreSQL can reparse this single varchar[]->text[] CHECK during restore.
    assert [row for row in source if row[0]!='terrain_business_status_valid']==[row for row in target if row[0]!='terrain_business_status_valid']
    allowed=['UNREVIEWED','WAITING_DEPENDENCY','CONFIRMED','NEEDS_RECONCILIATION','NOT_APPLIED','SUPERSEDED']
    for db in (connection,restored):
        with db.cursor() as cursor:
            cursor.execute("SELECT pg_get_expr(conbin,conrelid) FROM pg_constraint WHERE conname='terrain_business_status_valid' AND convalidated AND conrelid='core_terrainoutcome'::regclass")
            expression=cursor.fetchone()[0]
            for value in allowed+['INVALID','']:
                evaluated=re.sub(r'\bbusiness_status\b',"'"+value+"'",expression)
                cursor.execute('SELECT '+evaluated)
                assert cursor.fetchone()[0] == (value in allowed)
    checks['constraint_definitions_except_reparsed_status']=digest([row for row in source if row[0]!='terrain_business_status_valid'])
    checks['status_constraint_semantics']=True
    mutation_checks={}
    for table in ('core_auditevent','core_terrainsubmission','core_terraindecision','core_terrainstockadjustment'):
        for action in ('UPDATE','DELETE'):
            with restored.cursor() as cursor:
                try:cursor.execute(f'UPDATE {table} SET id=id' if action=='UPDATE' else f'DELETE FROM {table}')
                except psycopg2.Error as error:
                    assert error.pgcode=='P0001', (table,action,error.pgcode)
                    assert any(message in str(error) for message in ('append-only','immutable')), str(error)
                    mutation_checks[f'{table}:{action}']=error.pgcode
                else:raise AssertionError(f'{table} accepted {action}')
    restored.close()
    (PROOF/'postgres18-4-rehearsal.json').write_text(json.dumps({'synthetic_only':True,'postgres_version':version,'farms':20,'users':60,'payments':2000,'allocations':20,'money_preserved':True,'invalid_money_refused':rejected,'membership_bootstrap':True,'migration_seconds':durations,'sampled_locks':sorted(locks),'lock_sampling_interval_seconds':.01,'concurrent_load_test':False,'restore_checks':checks,'mutation_rejections':mutation_checks,'archive_sha256':hashlib.sha256(archive.read_bytes()).hexdigest()},indent=2)+'\n')
    print('PHASE2K_POSTGRES18_4_UPGRADE_AND_RESTORE_PASSED')

if __name__=='__main__':main()
