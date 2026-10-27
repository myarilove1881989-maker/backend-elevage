"""Identity and migration ordering tests; never connect to a database."""
import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from django.core.management.base import CommandError
from core import pilot_checks
from core.management.commands import pilot_deploy


class PilotIdentityTests(unittest.TestCase):
    def test_owner_email_must_match_explicit_configuration(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(pilot_deploy.expected_owner_email(), 'pilot-owner@example.invalid')
        with patch.dict(os.environ, PILOT_OWNER_EMAIL='owner@example.test'):
            self.assertEqual(pilot_deploy.expected_owner_email(), 'owner@example.test')
        with patch.dict(os.environ, PILOT_OWNER_EMAIL='invalid'), self.assertRaises(CommandError):
            pilot_deploy.expected_owner_email()

    def verify(self, *, vendor='postgresql', host='dpg-synthetic-a', name='elevage_pilot',
               role='elevage_pilot_owner', row=('elevage_pilot','elevage_pilot_owner','18.4'),
               module='config.pilot_settings'):
        conn = MagicMock()
        conn.vendor = vendor
        conn.settings_dict = {'HOST':host,'NAME':name,'USER':role}
        cursor = conn.cursor.return_value.__enter__.return_value
        cursor.fetchone.return_value = row
        cursor.fetchall.return_value = []
        with patch.object(pilot_checks, 'validate_environment'), patch.dict(os.environ, PILOT_DB_HOST='dpg-synthetic-a'), \
             patch.object(pilot_checks, 'connection', conn), \
             patch.object(pilot_checks, 'settings', SimpleNamespace(SETTINGS_MODULE=module, APP_ENV='pilot')):
            return pilot_checks.verify_database(), conn

    def test_correct_identity(self):
        result, _ = self.verify()
        self.assertEqual(result['tables'], [])

    def test_wrong_configuration_refused_before_connection(self):
        for change in ({'vendor':'sqlite'}, {'host':'dpg-production-a'}, {'name':'elevage'},
                       {'role':'production_owner'}, {'module':'config.settings'}):
            with self.subTest(change=change), self.assertRaises(CommandError):
                self.verify(**change)

    def test_connected_identity_not_just_url(self):
        for row in (('elevage','elevage_pilot_owner','18.4'),
                    ('elevage_pilot','production_owner','18.4'),
                    ('elevage_pilot','elevage_pilot_owner','17.0')):
            with self.subTest(row=row), self.assertRaises(CommandError):
                self.verify(row=row)

    def test_identity_failure_never_migrates(self):
        with patch.object(pilot_deploy, 'verify_database', side_effect=CommandError('wrong identity')), \
             patch.object(pilot_deploy, 'call_command') as migrate:
            with self.assertRaises(CommandError):
                pilot_deploy.Command().handle()
            migrate.assert_not_called()

    def test_unknown_nonempty_schema_never_migrates(self):
        with patch.object(pilot_deploy, 'verify_database', return_value={'tables':['foreign_table']}), \
             patch.object(pilot_deploy, 'call_command') as migrate:
            with self.assertRaises(CommandError):
                pilot_deploy.Command().handle()
            migrate.assert_not_called()

    def test_contended_lock_never_migrates(self):
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value.fetchone.return_value = (False,)
        with patch.object(pilot_deploy, 'verify_database', return_value={'tables':[]}), \
             patch.object(pilot_deploy, 'connection', conn), \
             patch.object(pilot_deploy, 'call_command') as migrate:
            with self.assertRaises(CommandError):
                pilot_deploy.Command().handle()
            migrate.assert_not_called()


if __name__ == '__main__':
    unittest.main()
