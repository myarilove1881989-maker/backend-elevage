"""Only the reviewed additive 0049 -> 0050 migration, with explicit backup gate."""
import hashlib,os
from pathlib import Path

MIGRATION_SHA256='a5e8b69c2ef26b717ea56311751220a6e2cee2921348539be31bb9aeb4a6b129'

def upgrade_responsibles(connection):
    from django.core.management import call_command
    from django.db import transaction
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.operations.models import CreateModel,AddField,AddConstraint
    from .production_upgrade import historical_snapshot,LOCK_KEY
    if connection.vendor!='postgresql':raise RuntimeError('POSTGRESQL_REQUIRED')
    target=('core','0050_health_responsibles');previous=('core','0049_health_fact_history')
    executor=MigrationExecutor(connection)
    def registry():
        with connection.cursor() as c:
            c.execute("SELECT name FROM django_migrations WHERE app='core'")
            return {('core',row[0]) for row in c.fetchall()}
    before_core={node for node in executor.loader.graph.forwards_plan(previous) if node[0]=='core'}
    after_core=before_core|{target}
    if registry()==after_core:
        if executor.migration_plan(executor.loader.graph.leaf_nodes()):raise RuntimeError('UNEXPECTED_PENDING_MIGRATIONS')
        print('ELEVAGE_SCHEMA_READY_0050',flush=True);return
    if registry()!=before_core:raise RuntimeError('UNEXPECTED_SCHEMA_STATE')
    backup=os.environ.get('ELEVAGE_HEALTH_BACKUP_SHA256','')
    if os.environ.get('ELEVAGE_ALLOW_HEALTH_UPGRADE')!='true' or len(backup)!=64 or any(ch not in '0123456789abcdefABCDEF' for ch in backup):
        raise RuntimeError('EXPLICIT_HEALTH_UPGRADE_AND_BACKUP_REQUIRED')
    plan=executor.migration_plan(executor.loader.graph.leaf_nodes())
    if len(plan)!=1 or plan[0][1] or (plan[0][0].app_label,plan[0][0].name)!=target or not plan[0][0].atomic:
        raise RuntimeError('HEALTH_MIGRATION_PLAN_REFUSED')
    ops=plan[0][0].operations
    if [type(op) for op in ops]!=[CreateModel,AddField,AddConstraint]:raise RuntimeError('HEALTH_MIGRATION_NOT_ADDITIVE')
    path=Path(__file__).parent/'migrations/0050_health_responsibles.py'
    if hashlib.sha256(path.read_bytes()).hexdigest()!=MIGRATION_SHA256:raise RuntimeError('HEALTH_MIGRATION_CONTENT_CHANGED')
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as c:
            c.execute("SET LOCAL lock_timeout='5s'");c.execute("SET LOCAL statement_timeout='120s'")
            c.execute('SELECT pg_try_advisory_xact_lock(%s,%s)',LOCK_KEY)
            if c.fetchone()!=(True,):raise RuntimeError('UPGRADE_ALREADY_RUNNING')
            tables=sorted(connection.introspection.table_names(c))
            c.execute('LOCK TABLE '+','.join(connection.ops.quote_name(t) for t in tables)+' IN ACCESS EXCLUSIVE MODE')
        if registry()!=before_core:raise RuntimeError('SCHEMA_CHANGED_BEFORE_LOCK')
        baseline=historical_snapshot(connection)
        call_command('migrate','core',target[1],interactive=False,verbosity=0)
        if registry()!=after_core or historical_snapshot(connection,baseline)!=baseline:raise RuntimeError('HISTORICAL_DATA_CHANGED')
        call_command('migrate',check=True,interactive=False,verbosity=0)
    print('ELEVAGE_HEALTH_UPGRADE_COMMITTED_HISTORY_UNCHANGED',flush=True)
