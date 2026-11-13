"""Exact, opt-in additive schema gate. Never invoked by ordinary API reads."""
import hashlib
import json
import os
from pathlib import Path
from django.db import transaction
from django.db.migrations.executor import MigrationExecutor
from core.production_upgrade import historical_snapshot, LOCK_KEY


def upgrade(connection):
    if connection.vendor!='postgresql':raise RuntimeError('POSTGRESQL_REQUIRED')
    root=Path(__file__).resolve().parents[1]
    manifest=json.loads((Path(__file__).with_name('migration_manifest.json')).read_text())
    approved=[]
    for file,digest in manifest.items():
        if hashlib.sha256((root/file).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('AQUATIC_MIGRATION_CONTENT_CHANGED')
        parts=Path(file).parts;approved.append((parts[0],Path(file).stem))
    executor=MigrationExecutor(connection)
    core_base={node for node in executor.loader.graph.forwards_plan(('core','0050_health_responsibles')) if node[0]=='core'}
    def registry():
        with connection.cursor() as cursor:
            cursor.execute("SELECT app,name FROM django_migrations WHERE app IN ('core','pisciculture')")
            return set(cursor.fetchall())
    target=core_base|set(approved)
    current=registry()
    plan=executor.migration_plan(executor.loader.graph.leaf_nodes())
    if current==target and not plan:
        return
    phase1=core_base|set(approved[:3])
    phase2a=core_base|set(approved[:7])
    phase2b=core_base|set(approved[:10])
    phase2c=core_base|set(approved[:13])
    phase3a=core_base|set(approved[:16])
    phase3b=core_base|set(approved[:19])
    if current not in (core_base,phase1,phase2a,phase2b,phase2c,phase3a,phase3b):raise RuntimeError('AQUATIC_UNEXPECTED_SCHEMA_STATE')
    expected=approved if current==core_base else approved[3:] if current==phase1 else approved[7:] if current==phase2a else approved[10:] if current==phase2b else approved[13:] if current==phase2c else approved[16:] if current==phase3a else approved[19:]
    if [(m.app_label,m.name) for m,backward in plan]!=expected or any(backward or not m.atomic for m,backward in plan):
        raise RuntimeError('AQUATIC_MIGRATION_PLAN_REFUSED')
    backup=os.environ.get('ELEVAGE_AQUATIC_BACKUP_SHA256','')
    if os.environ.get('ELEVAGE_ALLOW_AQUATIC_UPGRADE')!='true' or len(backup)!=64 or any(ch not in '0123456789abcdefABCDEF' for ch in backup):
        raise RuntimeError('EXPLICIT_AQUATIC_UPGRADE_AND_BACKUP_REQUIRED')
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='5s'")
            cursor.execute("SET LOCAL statement_timeout='120s'")
            cursor.execute('SELECT pg_try_advisory_xact_lock(%s,%s)',LOCK_KEY)
            if cursor.fetchone()!=(True,):raise RuntimeError('UPGRADE_ALREADY_RUNNING')
            tables=sorted(connection.introspection.table_names(cursor))
            cursor.execute('LOCK TABLE '+','.join(connection.ops.quote_name(t) for t in tables)+' IN ACCESS EXCLUSIVE MODE')
        if registry()!=current:raise RuntimeError('SCHEMA_CHANGED_BEFORE_LOCK')
        baseline=historical_snapshot(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        if registry()!=target or historical_snapshot(connection,baseline)!=baseline:
            raise RuntimeError('HISTORICAL_DATA_CHANGED')
