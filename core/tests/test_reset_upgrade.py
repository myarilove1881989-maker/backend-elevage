"""Real PostgreSQL rehearsal of the exact 0037 -> 0038 gate, never production."""
import io, os
from contextlib import redirect_stdout
from unittest import skipUnless
from unittest.mock import patch
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from core.models import User
from core.production_upgrade import upgrade_password_reset, historical_snapshot

@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL transactional DDL required')
class ResetUpgradeTests(TransactionTestCase):
    def setUp(self):
        User.objects.create_user(username='upgrade-synthetic-owner', email='upgrade@example.test', password='SyntheticPassword!827')
        MigrationExecutor(connection).migrate([('core','0037_offline_business_revocation')])
        self.before = historical_snapshot(connection)

    def tearDown(self):
        MigrationExecutor(connection).migrate([('core','0038_passwordresetlimit')])

    def test_unapproved_or_missing_backup_leaves_all_historical_rows_untouched(self):
        for flag, backup in [('false','a'*64), ('true',''), ('true','not-a-digest')]:
            with patch.dict(os.environ, {'ELEVAGE_ALLOW_RESET_UPGRADE':flag,'ELEVAGE_RESET_BACKUP_SHA256':backup}):
                with self.assertRaisesMessage(RuntimeError,'EXPLICIT_RESET_UPGRADE_AND_BACKUP_REQUIRED'):
                    upgrade_password_reset(connection)
            self.assertEqual(historical_snapshot(connection),self.before)
            self.assertNotIn('core_passwordresetemail',connection.introspection.table_names())

    @patch.dict(os.environ, {'ELEVAGE_ALLOW_RESET_UPGRADE':'true','ELEVAGE_RESET_BACKUP_SHA256':'a'*64})
    def test_additive_upgrade_preserves_data_and_restart_is_read_only(self):
        with redirect_stdout(io.StringIO()) as output:
            upgrade_password_reset(connection)
        self.assertIn('ELEVAGE_RESET_UPGRADE_COMMITTED',output.getvalue())
        self.assertEqual(historical_snapshot(connection,self.before),self.before)
        tables=connection.introspection.table_names()
        self.assertIn('core_passwordresetemail',tables)
        self.assertIn('core_passwordresetlimit',tables)
        after=historical_snapshot(connection)
        with patch.dict(os.environ, {'ELEVAGE_ALLOW_RESET_UPGRADE':'false','ELEVAGE_RESET_BACKUP_SHA256':''}):
            with redirect_stdout(io.StringIO()) as output:
                upgrade_password_reset(connection)
        self.assertIn('ELEVAGE_SCHEMA_READY_0038',output.getvalue())
        self.assertEqual(historical_snapshot(connection),after)

    @patch.dict(os.environ, {'ELEVAGE_ALLOW_RESET_UPGRADE':'true','ELEVAGE_RESET_BACKUP_SHA256':'a'*64})
    def test_history_failure_rolls_back_schema_and_registry(self):
        original=historical_snapshot
        def altered(conn,baseline=None):
            if baseline is not None: return {'synthetic-mismatch':True}
            return original(conn,baseline)
        with patch('core.production_upgrade.historical_snapshot',side_effect=altered):
            with self.assertRaisesMessage(RuntimeError,'HISTORICAL_DATA_CHANGED'):
                upgrade_password_reset(connection)
        self.assertEqual(historical_snapshot(connection),self.before)
        self.assertNotIn('core_passwordresetemail',connection.introspection.table_names())

    def test_incomplete_or_foreign_registry_is_refused_before_writes(self):
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM django_migrations WHERE app='core' AND name='0036_expense_cancellation_immutable'")
        try:
            with self.assertRaisesMessage(RuntimeError,'UNEXPECTED_SCHEMA_STATE'):
                upgrade_password_reset(connection)
            self.assertEqual(historical_snapshot(connection),self.before)
        finally:
            from django.db.migrations.recorder import MigrationRecorder
            MigrationRecorder(connection).record_applied('core','0036_expense_cancellation_immutable')
