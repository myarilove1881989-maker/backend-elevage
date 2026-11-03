"""Explicit atomic 0038 -> 0049 release, preserving historical business rows."""
import hashlib
import json
import os
from pathlib import Path

# Filled from the reviewed migration files by the release preparation script.
MIGRATION_HASHES = {'0039_supply_foundation.py': '4ba6edb201871d827600c9cd92ff628a67d0d199dd14b5bac91de4f2c90147ca', '0040_supply_history_protection.py': '797383f6337007f044cdcc940afa50110ba1b8077df3eebb28484e86b9b0a995', '0041_supply_foundation.py': '6580d13a9ac906f6b0385f0a85a095fc92220280f87a38cbc0ddfb47fff708d8', '0042_supply_foundation.py': '11e414f9df8b581035fc2817f3ae2650f6d9ce459220d8e8b698f98ccee55b1a', '0043_feed_category_bindings.py': '8e7292d8c0c9dc2300591ad683d7d6cc3c1021c987b405674a3352062ee2319e', '0044_supply_foundation.py': '354c877791de1515da14fde103909ad2ba0d291a513a62a9197a11e8133c4db7', '0045_feed_link_history.py': 'cb310b973391284d7e27386143a8c7c4fe754c74567e19785c8f5c51a42eb5e4', '0046_vaccination_and_more.py': '09e0e664627e63e0a35c7220b0db42aaba12aa6ce21d492ccd78180845ca72c8', '0047_vaccine_category_bindings.py': 'cecfd02405e2193919256171a4b69c75e5fc31148cd39f969c934330dfe575f8', '0048_healthtask_vaccinationtasklink_healthcommand.py': '6d4a38057bcb0eb4e0e7097e12609088b6a2badf9d5722941d83b82ad3590eb0', '0049_health_fact_history.py': 'ca3213b3ee367507de186d1911322d746894a583263bd2d7e3d54cba3ffc46a6'}

def upgrade_stocks(connection):
    from django.core.management import call_command
    from django.db import transaction
    from django.db.migrations.executor import MigrationExecutor
    from core.production_upgrade import historical_snapshot, LOCK_KEY
    if connection.vendor != 'postgresql':
        raise RuntimeError('POSTGRESQL_REQUIRED')
    target=('core','0049_health_fact_history')
    previous=('core','0038_passwordresetlimit')
    executor=MigrationExecutor(connection)
    before_registry=set(executor.loader.graph.forwards_plan(previous))
    after_registry=set(executor.loader.graph.forwards_plan(target))
    before_core={n for n in before_registry if n[0]=='core'}
    after_core={n for n in after_registry if n[0]=='core'}
    def registry():
        with connection.cursor() as c:
            c.execute("SELECT name FROM django_migrations WHERE app='core'")
            return {('core',r[0]) for r in c.fetchall()}
    actual=registry()
    if actual==after_core:
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):
            raise RuntimeError('UNEXPECTED_PENDING_MIGRATIONS')
        print('ELEVAGE_SCHEMA_READY_0049',flush=True)
        return
    if actual!=before_core:raise RuntimeError('UNEXPECTED_SCHEMA_STATE')
    backup=os.environ.get('ELEVAGE_STOCKS_BACKUP_SHA256','')
    if os.environ.get('ELEVAGE_ALLOW_STOCKS_UPGRADE')!='true' or len(backup)!=64 or any(c not in '0123456789abcdefABCDEF' for c in backup):
        raise RuntimeError('EXPLICIT_STOCKS_UPGRADE_AND_BACKUP_REQUIRED')
    plan=executor.migration_plan(executor.loader.graph.leaf_nodes())
    expected=after_core-before_core
    if len(plan)!=11 or {(m.app_label,m.name) for m,_ in plan}!=expected or any(backward or not m.atomic for m,backward in plan):
        raise RuntimeError('STOCKS_MIGRATION_PLAN_REFUSED')
    directory=Path(__file__).parent/'migrations'
    if set(MIGRATION_HASHES)!={m.name+'.py' for m,_ in plan}:
        raise RuntimeError('STOCKS_MIGRATION_MANIFEST_REFUSED')
    for name,digest in MIGRATION_HASHES.items():
        if hashlib.sha256((directory/name).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('STOCKS_MIGRATION_CONTENT_CHANGED')
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as c:
            c.execute("SET LOCAL lock_timeout='5s'")
            c.execute("SET LOCAL statement_timeout='120s'")
            c.execute('SELECT pg_try_advisory_xact_lock(%s,%s)',LOCK_KEY)
            if c.fetchone()!=(True,):raise RuntimeError('UPGRADE_ALREADY_RUNNING')
            tables=sorted(connection.introspection.table_names(c))
            c.execute('LOCK TABLE '+','.join(connection.ops.quote_name(t) for t in tables)+' IN ACCESS EXCLUSIVE MODE')
            c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        if registry()!=before_core:raise RuntimeError('SCHEMA_CHANGED_BEFORE_LOCK')
        baseline=historical_snapshot(connection)
        call_command('migrate','core',target[1],interactive=False,verbosity=0)
        observed=historical_snapshot(connection,baseline)
        if observed!=baseline:
            print('ELEVAGE_STOCKS_HISTORY_DIFFERENCES '+json.dumps({k:{'before':{'count':baseline[k]['count'],'sha256':baseline[k]['sha256']},'after':{'count':observed.get(k,{}).get('count'),'sha256':observed.get(k,{}).get('sha256')}} for k in baseline if observed.get(k)!=baseline[k]},sort_keys=True),flush=True)
        if registry()!=after_core or observed!=baseline:
            raise RuntimeError('HISTORICAL_DATA_CHANGED')
        with connection.cursor() as c:c.execute('SET CONSTRAINTS ALL IMMEDIATE')
        call_command('migrate',check=True,interactive=False,verbosity=0)
    print('ELEVAGE_STOCKS_UPGRADE_COMMITTED '+json.dumps({'core_schema':target[1],'historical_tables_checked':len(baseline),'historical_rows_checked':sum(v['count'] for v in baseline.values()),'historical_data_unchanged':True,'backup_sha256':backup.lower()},sort_keys=True),flush=True)
