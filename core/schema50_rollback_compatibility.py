"""Read-only acceptance of the reviewed additive schema for the previous backend."""
def verify_schema50(connection):
    from django.db import transaction
    from django.db.migrations.executor import MigrationExecutor
    if connection.vendor != 'postgresql':
        raise RuntimeError('POSTGRESQL_REQUIRED')
    with transaction.atomic(using=connection.alias):
        with connection.cursor() as cursor:
            cursor.execute('SET TRANSACTION READ ONLY')
        executor = MigrationExecutor(connection)
        previous = ('core', '0049_health_fact_history')
        expected = {node for node in executor.loader.graph.forwards_plan(previous) if node[0] == 'core'}
        expected.add(('core', '0050_health_responsibles'))
        with connection.cursor() as cursor:
            cursor.execute("SELECT name FROM django_migrations WHERE app='core'")
            actual = {('core', row[0]) for row in cursor.fetchall()}
            if actual != expected:
                raise RuntimeError('UNEXPECTED_SCHEMA_STATE')
            if executor.migration_plan(executor.loader.graph.leaf_nodes()):
                raise RuntimeError('UNEXPECTED_PENDING_MIGRATIONS')
            cursor.execute("SELECT is_nullable,data_type FROM information_schema.columns WHERE table_schema='public' AND table_name='core_healthtask' AND column_name='responsible_id'")
            if cursor.fetchone() != ('YES', 'bigint'):
                raise RuntimeError('SCHEMA50_RELATION_INVALID')
            cursor.execute("SELECT to_regclass('public.core_healthresponsible') IS NOT NULL")
            if cursor.fetchone() != (True,):
                raise RuntimeError('SCHEMA50_CONTACTS_MISSING')
            cursor.execute("SELECT count(*) FROM pg_constraint WHERE conrelid='core_healthtask'::regclass AND confrelid='core_healthresponsible'::regclass AND contype='f'")
            if cursor.fetchone() != (1,):
                raise RuntimeError('SCHEMA50_FOREIGN_KEY_INVALID')
    print('ELEVAGE_SCHEMA50_ROLLBACK_READY_READ_ONLY', flush=True)
