import io,os
from contextlib import redirect_stdout
from unittest import skipUnless
from unittest.mock import patch
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import TransactionTestCase
from core.production_upgrade import historical_snapshot
from core.stocks_upgrade import upgrade_stocks

@skipUnless(connection.vendor=='postgresql','PostgreSQL required')
class StocksUpgradeTests(TransactionTestCase):
    def setUp(self):
        MigrationExecutor(connection).migrate([('core','0038_passwordresetlimit')])
        from core.models import User,PasswordResetCode,PasswordResetEmail
        from django.utils import timezone
        from datetime import timedelta
        user=User.objects.create_user(username='binary-upgrade-test')
        code=PasswordResetCode.objects.create(user=user,code_hash='synthetic',expires_at=timezone.now()+timedelta(minutes=5))
        PasswordResetEmail.objects.create(code=code,encrypted_code=b'\x00\x01synthetic-ciphertext\xff')
        # Exercise the historical 0038 -> 0049 release against its actual graph.
        # The current 0050 migration has a separate approval/backup gate.
        original_load=MigrationLoader.load_disk
        def load_historical(loader):
            original_load(loader)
            loader.disk_migrations.pop(('core','0050_health_responsibles'),None)
        self.historical_loader=patch.object(MigrationLoader,'load_disk',load_historical)
        self.historical_loader.start()
        self.before=historical_snapshot(connection)
        assert historical_snapshot(connection)==self.before
    def tearDown(self):
        self.historical_loader.stop()
        executor=MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
    def test_missing_backup_refuses_writes(self):
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_STOCKS_UPGRADE':'true','ELEVAGE_STOCKS_BACKUP_SHA256':''}):
            with self.assertRaisesMessage(RuntimeError,'EXPLICIT_STOCKS_UPGRADE_AND_BACKUP_REQUIRED'):upgrade_stocks(connection)
        self.assertEqual(historical_snapshot(connection),self.before)
    @patch.dict(os.environ,{'ELEVAGE_ALLOW_STOCKS_UPGRADE':'true','ELEVAGE_STOCKS_BACKUP_SHA256':'a'*64})
    def test_upgrade_and_restart_preserve_historical_rows(self):
        with redirect_stdout(io.StringIO()):upgrade_stocks(connection)
        self.assertEqual(historical_snapshot(connection,self.before),self.before)
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_STOCKS_UPGRADE':'false'}):
            with redirect_stdout(io.StringIO()) as output:upgrade_stocks(connection)
        self.assertIn('ELEVAGE_SCHEMA_READY_0049',output.getvalue())
    @patch.dict(os.environ,{'ELEVAGE_ALLOW_STOCKS_UPGRADE':'true','ELEVAGE_STOCKS_BACKUP_SHA256':'a'*64})
    def test_historical_failure_rolls_back_all_eleven_migrations(self):
        original=historical_snapshot
        def changed(conn,baseline=None):return {'changed':True} if baseline else original(conn)
        with patch('core.production_upgrade.historical_snapshot',side_effect=changed):
            with self.assertRaisesMessage(RuntimeError,'HISTORICAL_DATA_CHANGED'):upgrade_stocks(connection)
        self.assertEqual(historical_snapshot(connection),self.before)
        with connection.cursor() as c:
            c.execute("SELECT count(*) FROM django_migrations WHERE app='core'");self.assertEqual(c.fetchone(),(38,))
    @patch.dict(os.environ,{'ELEVAGE_ALLOW_STOCKS_UPGRADE':'true','ELEVAGE_STOCKS_BACKUP_SHA256':'a'*64})
    def test_unreviewed_migration_manifest_refuses_writes(self):
        with patch('core.stocks_upgrade.MIGRATION_HASHES',{}):
            with self.assertRaisesMessage(RuntimeError,'STOCKS_MIGRATION_MANIFEST_REFUSED'):upgrade_stocks(connection)
        self.assertEqual(historical_snapshot(connection),self.before)

    @patch.dict(os.environ,{'ELEVAGE_ALLOW_STOCKS_UPGRADE':'true','ELEVAGE_STOCKS_BACKUP_SHA256':'a'*64})
    def test_current_extra_migration_remains_refused_by_old_release_guard(self):
        self.historical_loader.stop()
        with self.assertRaisesMessage(RuntimeError,'STOCKS_MIGRATION_PLAN_REFUSED'):
            upgrade_stocks(connection)
        self.assertEqual(historical_snapshot(connection),self.before)
        with connection.cursor() as c:
            c.execute("SELECT count(*) FROM django_migrations WHERE app='core'")
            self.assertEqual(c.fetchone(),(38,))
