"""Explicit, atomic principal-service upgrade. Never repairs business records."""
from decimal import Decimal
import hashlib
import io
import json
import os
import sys

SERVICE_ID = 'srv-d7sdmucm0tmc73cvc6u0'
LOCK_KEY = (20261027, 104)


def historical_snapshot(connection, baseline=None):
    result = {}
    with connection.cursor() as cursor:
        available = set(connection.introspection.table_names(cursor))
        wanted = baseline or {name: None for name in available if name.startswith('core_')}
        for name, old in sorted(wanted.items()):
            if name not in available:
                raise RuntimeError('HISTORICAL_TABLE_MISSING')
            columns = old['columns'] if old else [item.name for item in
                connection.introspection.get_table_description(cursor, name)
                if item.name not in {'last_login', 'last_activity'}]
            quote = connection.ops.quote_name
            cursor.execute('SELECT ' + ','.join(map(quote, columns)) + ' FROM ' +
                quote(name) + ' ORDER BY ' + quote('id'))
            digest, count = hashlib.sha256(), 0
            while rows := cursor.fetchmany(500):
                for row in rows:
                    values = list(row)
                    if name in {'core_payment', 'core_lettrage'} and 'montant' in columns:
                        index = columns.index('montant')
                        amount = Decimal(str(values[index]))
                        if not amount.is_finite() or not 0 <= amount <= Decimal('9999999999.99'):
                            raise RuntimeError('MONEY_PREFLIGHT_REQUIRED')
                        rounded = amount.quantize(Decimal('.01'))
                        if abs(amount - rounded) > Decimal('.0000001'):
                            raise RuntimeError('MONEY_PREFLIGHT_REQUIRED')
                        values[index] = str(rounded)
                    digest.update(json.dumps(values, default=str, ensure_ascii=False).encode())
                    digest.update(b'\n')
                    count += 1
            result[name] = {'columns': columns, 'count': count, 'sha256': digest.hexdigest()}
    return result



def upgrade_password_reset(connection):
    """Only the exact, approved additive 0037 -> 0038 plan may write."""
    from django.core.management import call_command
    from django.db import transaction
    from django.db.migrations.executor import MigrationExecutor

    target = ('core', '0038_passwordresetlimit')
    previous = ('core', '0037_offline_business_revocation')
    executor = MigrationExecutor(connection)
    expected_before = {node for node in executor.loader.graph.forwards_plan(previous) if node[0] == 'core'}
    expected_after = expected_before | {target}

    def registry():
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM django_migrations WHERE app='core'")
            return {('core', row[0]) for row in cursor.fetchall()}

    actual = registry()
    if actual == expected_after:
        call_command('migrate', check=True, interactive=False, verbosity=0)
        print('ELEVAGE_SCHEMA_READY_0038', flush=True)
        return
    if actual != expected_before:
        raise RuntimeError('UNEXPECTED_SCHEMA_STATE')
    backup = os.environ.get('ELEVAGE_RESET_BACKUP_SHA256', '')
    if (os.environ.get('ELEVAGE_ALLOW_RESET_UPGRADE') != 'true' or
            len(backup) != 64 or any(c not in '0123456789abcdefABCDEF' for c in backup)):
        raise RuntimeError('EXPLICIT_RESET_UPGRADE_AND_BACKUP_REQUIRED')
    plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
    if len(plan) != 1 or plan[0][1] or (plan[0][0].app_label, plan[0][0].name) != target or not plan[0][0].atomic:
        raise RuntimeError('RESET_MIGRATION_PLAN_REFUSED')
    operations = plan[0][0].operations
    from django.db.migrations.operations.models import CreateModel
    if len(operations) != 2 or any(type(op) is not CreateModel for op in operations) or {op.name for op in operations} != {'PasswordResetLimit', 'PasswordResetEmail'}:
        raise RuntimeError('RESET_MIGRATION_NOT_ADDITIVE')

    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='5s'")
            cursor.execute("SET LOCAL statement_timeout='60s'")
            cursor.execute('SELECT pg_try_advisory_xact_lock(%s,%s)', LOCK_KEY)
            if cursor.fetchone() != (True,):
                raise RuntimeError('UPGRADE_ALREADY_RUNNING')
            tables = sorted(connection.introspection.table_names(cursor))
            cursor.execute('LOCK TABLE ' + ','.join(connection.ops.quote_name(name) for name in tables) + ' IN SHARE ROW EXCLUSIVE MODE')
        if registry() != expected_before:
            raise RuntimeError('SCHEMA_CHANGED_BEFORE_LOCK')
        before = historical_snapshot(connection)
        call_command('migrate', 'core', target[1], interactive=False, verbosity=0)
        if registry() != expected_after or historical_snapshot(connection, before) != before:
            raise RuntimeError('HISTORICAL_DATA_CHANGED')
        call_command('migrate', check=True, interactive=False, verbosity=0)
    print('ELEVAGE_RESET_UPGRADE_COMMITTED ' + json.dumps({
        'historical_data_unchanged': True, 'historical_tables_checked': len(before),
        'historical_rows_checked': sum(item['count'] for item in before.values()),
        'backup_sha256': backup.lower(), 'core_schema': target[1],
    }, sort_keys=True), flush=True)


def upgrade():
    os.environ['DJANGO_SETTINGS_MODULE'] = 'config.settings_production'
    os.environ['APP_ENV'] = 'production'
    import django
    django.setup()
    from .offline_readiness import report
    report()
    from django.core.management import call_command
    from django.db import connection, transaction
    from django.db.migrations.executor import MigrationExecutor

    if connection.vendor != 'postgresql':
        raise RuntimeError('POSTGRESQL_REQUIRED')
    with connection.cursor() as cursor:
        cursor.execute('SELECT inet_server_port(), current_database(), ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')
        port, database, tls = cursor.fetchone()
        local = (os.environ.get('ELEVAGE_UPGRADE_LOCAL_REHEARSAL') == 'true' and
            connection.settings_dict['HOST'] == '127.0.0.1' and port == 57827 and
            database == 'elevage_atomic_rehearsal')
        if not local and (os.environ.get('RENDER_SERVICE_ID') != SERVICE_ID or database != 'elevage'):
            raise RuntimeError('UNAUTHORIZED_TARGET')
        if not tls:
            raise RuntimeError('TLS_REQUIRED')
        cursor.execute("SELECT count(*),max(name) FROM django_migrations WHERE app='core'")
        core_count, core_latest = cursor.fetchone()
    if (core_count, core_latest) in {
            (37, '0037_offline_business_revocation'), (38, '0038_passwordresetlimit')}:
        upgrade_password_reset(connection)
        return
    if (core_count, core_latest) != (15, '0015_mouvement_lot_origine'):
        raise RuntimeError('UNEXPECTED_SCHEMA_STATE')
    backup = os.environ.get('ELEVAGE_APPROVED_BACKUP_SHA256', '')
    if os.environ.get('ELEVAGE_ALLOW_USERS_UPGRADE') != 'true' or len(backup) != 64 or any(c not in '0123456789abcdefABCDEF' for c in backup):
        raise RuntimeError('EXPLICIT_UPGRADE_AND_BACKUP_REQUIRED')
    # Refuse a non-atomic migration plan before taking application table locks.
    executor = MigrationExecutor(connection)
    targets = executor.loader.graph.leaf_nodes()
    plan = executor.migration_plan(targets)
    if any(migration.app_label == 'core' and migration.name == '0038_passwordresetlimit' for migration, _ in plan):
        raise RuntimeError('LEGACY_SCHEMA_REQUIRES_0037_RELEASE')
    if any(backward or not migration.atomic for migration, backward in plan):
        raise RuntimeError('NON_ATOMIC_PLAN_REFUSED')

    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL lock_timeout='5s'")
            cursor.execute("SET LOCAL statement_timeout='60s'")
            cursor.execute('SELECT pg_try_advisory_xact_lock(%s,%s)', LOCK_KEY)
            if cursor.fetchone() != (True,):
                raise RuntimeError('UPGRADE_ALREADY_RUNNING')
            tables = sorted(connection.introspection.table_names(cursor))
            cursor.execute('LOCK TABLE ' + ','.join(connection.ops.quote_name(name) for name in tables) + ' IN ACCESS EXCLUSIVE MODE')
            # Validate deferred foreign keys immediately throughout this global
            # transaction, including constraints introduced by the migrations.
            cursor.execute('SET CONSTRAINTS ALL IMMEDIATE')
            cursor.execute("SELECT count(*),max(name) FROM django_migrations WHERE app='core'")
            if cursor.fetchone() != (15, '0015_mouvement_lot_origine'):
                raise RuntimeError('SCHEMA_CHANGED_BEFORE_LOCK')
        before = historical_snapshot(connection)
        sink = io.StringIO()
        for target in ('0022', '0031', '0037'):
            call_command('migrate', 'core', target, interactive=False, verbosity=0, stdout=sink)
            if historical_snapshot(connection, before) != before:
                raise RuntimeError('HISTORICAL_DATA_CHANGED')
        call_command('migrate', 'token_blacklist', interactive=False, verbosity=0, stdout=sink)
        call_command('migrate', check=True, interactive=False, verbosity=0, stdout=sink)
        if historical_snapshot(connection, before) != before:
            raise RuntimeError('HISTORICAL_DATA_CHANGED')
        from core.models import Exploitation, ExploitationMembership
        for farm in Exploitation.objects.all().iterator():
            owners = list(ExploitationMembership.objects.filter(exploitation_id=farm.pk, role='OWNER')
                .values_list('user_id', 'is_active'))
            if owners != [(farm.proprietaire_id, True)]:
                raise RuntimeError('OWNER_DIAGNOSTIC_REQUIRED')
        with connection.cursor() as cursor:
            cursor.execute('SELECT count(*) FROM django_migrations')
            if cursor.fetchone() != (67,):
                raise RuntimeError('UNEXPECTED_FINAL_REGISTRY')
    print('ELEVAGE_USERS_UPGRADE_COMMITTED ' + json.dumps({
        'historical_data_unchanged': True,
        'historical_tables_checked': len(before),
        'historical_rows_checked': sum(item['count'] for item in before.values()),
        'backup_sha256': backup.lower(),
    }, sort_keys=True), flush=True)


if __name__ == '__main__':
    try:
        upgrade()
    except Exception as error:
        # Never print DSNs, credential values, private rows or arbitrary SQL errors.
        code = str(error) if isinstance(error, RuntimeError) and str(error).isupper() and str(error).replace('_','').isalnum() else type(error).__name__
        print('ELEVAGE_USERS_UPGRADE_REFUSED ' + code, file=sys.stderr, flush=True)
        raise SystemExit(1)
