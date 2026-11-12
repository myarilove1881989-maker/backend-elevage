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
