import os
from unittest import skipUnless
from unittest.mock import patch
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from core.models import User
from core.production_upgrade import historical_snapshot
from core.health_responsible_upgrade import upgrade_responsibles
from pisciculture.upgrade import upgrade


@skipUnless(connection.vendor=='postgresql','Transactional migration qualification requires PostgreSQL')
class AquaticUpgradeTests(TransactionTestCase):
    def setUp(self):
        MigrationExecutor(connection).migrate([('pisciculture',None),('core','0050_health_responsibles')])
        User.objects.create_user(username='aquatic-upgrade-synthetic')
        self.before=historical_snapshot(connection)

    def tearDown(self):
        executor=MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def test_0050_guard_refuses_unapproved_new_schema_without_changes(self):
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'','ELEVAGE_AQUATIC_BACKUP_SHA256':''}):
            with self.assertRaisesRegex(RuntimeError,'BACKUP_REQUIRED'):upgrade_responsibles(connection)
        self.assertEqual(historical_snapshot(connection,self.before),self.before)
        self.assertNotIn('pisciculture_cohort',connection.introspection.table_names())

    def test_exact_upgrade_preserves_history_and_restart_needs_no_flag(self):
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'true','ELEVAGE_AQUATIC_BACKUP_SHA256':'a'*64}):
            upgrade(connection)
        self.assertEqual(historical_snapshot(connection,self.before),self.before)
        self.assertIn('pisciculture_cohort',connection.introspection.table_names())
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':''}):upgrade(connection)
        self.assertEqual(historical_snapshot(connection,self.before),self.before)

    def test_altered_manifest_refused(self):
        with patch('pisciculture.upgrade.hashlib.sha256') as digest:
            digest.return_value.hexdigest.return_value='0'*64
            with self.assertRaisesRegex(RuntimeError,'CONTENT_CHANGED'):upgrade(connection)
        self.assertEqual(historical_snapshot(connection,self.before),self.before)

    def test_phase1_to_phase2a_requires_opt_in_and_preserves_history(self):
        executor=MigrationExecutor(connection)
        executor.migrate([('core','0051_remove_membershippermission_membership_action_known_and_more'),('pisciculture','0002_immutable_facts')])
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'','ELEVAGE_AQUATIC_BACKUP_SHA256':''}):
            with self.assertRaisesRegex(RuntimeError,'BACKUP_REQUIRED'):upgrade(connection)
        self.assertNotIn('pisciculture_observation',connection.introspection.table_names())
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'true','ELEVAGE_AQUATIC_BACKUP_SHA256':'a'*64}):
            upgrade(connection)
        self.assertIn('pisciculture_observation',connection.introspection.table_names())
        self.assertEqual(historical_snapshot(connection,self.before),self.before)


    def test_startup_dispatch_accepts_exact_0052(self):
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'true','ELEVAGE_AQUATIC_BACKUP_SHA256':'a'*64}):
            upgrade(connection)
        from core.production_upgrade import upgrade as startup, SERVICE_ID
        original=connection.cursor
        class LocalCursor:
            def __init__(self):
                self.context=original();self.cursor=self.context;self.synthetic=None
            def __enter__(self):
                self.context.__enter__();return self
            def __exit__(self,*args):return self.context.__exit__(*args)
            def __iter__(self):return iter(self.cursor)
            def execute(self,sql,params=None):
                if 'current_database()' in sql:
                    self.synthetic=(5432,'elevage',True)
                else:
                    self.synthetic=None;self.cursor.execute(sql,params)
            def fetchone(self):
                if self.synthetic is not None:return self.synthetic
                return self.cursor.fetchone()
            def __getattr__(self,key):return getattr(self.cursor,key)
        # Only the Render target metadata and readiness are synthetic.
        # The real database, registry, manifest and migration plan remain checked.
        with patch('pisciculture.upgrade.upgrade',wraps=upgrade) as delegate:
            with patch.object(connection,'cursor',side_effect=LocalCursor),patch('django.setup'),patch('core.offline_readiness.report'):
                with patch.dict(os.environ,{'RENDER_SERVICE_ID':SERVICE_ID,'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'false'}):
                    startup()
            delegate.assert_called_once_with(connection)
