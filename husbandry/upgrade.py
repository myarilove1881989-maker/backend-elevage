"""Exact additive transition from the published aquatic schema. No reset path."""
import hashlib,json,os,io
from pathlib import Path
from django.db import transaction
from django.db.migrations.executor import MigrationExecutor
from core.production_upgrade import LOCK_KEY

def historical(connection,baseline=None):
    out={}
    with connection.cursor() as cursor:
        names=baseline or {n:None for n in connection.introspection.table_names(cursor) if n.startswith(('core_','pisciculture_'))}
        for name,old in sorted(names.items()):
            quote=connection.ops.quote_name
            columns=old['columns'] if old else [c.name for c in connection.introspection.get_table_description(cursor,name) if c.name not in {'last_login','last_activity'}]
            pk=old['pk'] if old else next(v['columns'] for v in connection.introspection.get_constraints(cursor,name).values() if v['primary_key'])
            cursor.execute('SELECT '+','.join(map(quote,columns))+' FROM '+quote(name)+' ORDER BY '+','.join(map(quote,pk)))
            digest=hashlib.sha256();count=0
            while rows:=cursor.fetchmany(500):
                for row in rows:
                    row=[bytes(v).hex() if isinstance(v,(bytes,bytearray,memoryview)) else v for v in row]
                    digest.update(json.dumps(row,default=str,ensure_ascii=False).encode());digest.update(b'\n');count+=1
            out[name]={'columns':columns,'pk':pk,'rows':count,'sha256':digest.hexdigest()}
    return out

def upgrade(connection):
    if connection.vendor!='postgresql':raise RuntimeError('POSTGRESQL_REQUIRED')
    root=Path(__file__).resolve().parents[1];manifest=json.loads(Path(__file__).with_name('migration_manifest.json').read_text())
    if hashlib.sha256((root/'pisciculture/migration_manifest.json').read_bytes()).hexdigest()!=manifest['aquatic_manifest_sha256']:raise RuntimeError('AQUATIC_MANIFEST_CHANGED')
    for file,digest in {**json.loads((root/'pisciculture/migration_manifest.json').read_text()),**manifest['migrations']}.items():
        if hashlib.sha256((root/file).read_bytes()).hexdigest()!=digest:raise RuntimeError('MIGRATION_CONTENT_CHANGED')
    base={(x['app'],x['name']) for x in manifest['baseline']};nodes=[('husbandry',Path(f).stem) for f in manifest['migrations']];target=base|set(nodes)
    def registry():
        with connection.cursor() as cursor:
            cursor.execute('SELECT app,name FROM django_migrations');return set(cursor.fetchall())
    current=registry();executor=MigrationExecutor(connection);plan=executor.migration_plan(executor.loader.graph.leaf_nodes())
    if current==target and not plan:return
    if current!=base:raise RuntimeError('HUSBANDRY_UNEXPECTED_SCHEMA_STATE')
    if [(m.app_label,m.name) for m,back in plan]!=nodes or any(back or not m.atomic for m,back in plan):raise RuntimeError('HUSBANDRY_MIGRATION_PLAN_REFUSED')
    backup=os.environ.get('ELEVAGE_HUSBANDRY_BACKUP_SHA256','')
    if os.environ.get('ELEVAGE_ALLOW_HUSBANDRY_UPGRADE')!='true' or len(backup)!=64 or any(c not in '0123456789abcdefABCDEF' for c in backup):raise RuntimeError('EXPLICIT_HUSBANDRY_UPGRADE_AND_BACKUP_REQUIRED')
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='5s'");cursor.execute("SET LOCAL statement_timeout='120s'")
            cursor.execute('SELECT pg_try_advisory_xact_lock(%s,%s)',LOCK_KEY)
            if cursor.fetchone()!=(True,):raise RuntimeError('UPGRADE_ALREADY_RUNNING')
            names=sorted(connection.introspection.table_names(cursor));cursor.execute('LOCK TABLE '+','.join(connection.ops.quote_name(n) for n in names)+' IN ACCESS EXCLUSIVE MODE')
        if registry()!=base:raise RuntimeError('SCHEMA_CHANGED_BEFORE_LOCK')
        before=historical(connection);executor.migrate(executor.loader.graph.leaf_nodes())
        if registry()!=target or historical(connection,before)!=before:raise RuntimeError('HISTORICAL_DATA_CHANGED')
    print('ELEVAGE_HUSBANDRY_UPGRADE_COMMITTED '+json.dumps({'historical_data_unchanged':True,'historical_tables_checked':len(before),'historical_rows_checked':sum(x['rows'] for x in before.values()),'backup_sha256':backup.lower(),'registry_count':len(target)}),flush=True)
