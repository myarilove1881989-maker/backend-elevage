import os
import sys
import unittest
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from config.pilot_policy import validate_environment


class PilotPolicyTests(unittest.TestCase):
    def setUp(self):
        self.env = dict(APP_ENV='pilot', PILOT_DB_HOST='dpg-synthetic123-a',
            PILOT_DB_NAME='elevage_pilot', PILOT_DB_USER='elevage_pilot_owner',
            DATABASE_URL='postgresql://elevage_pilot_owner:synthetic@dpg-synthetic123-a/elevage_pilot',
            PILOT_WEB_HOST='backend-elevage-pilot.onrender.com', SECRET_KEY='s'*64,
            PILOT_OWNER_PASSWORD='o'*64, PILOT_OPERATOR_PASSWORD='p'*64, PILOT_ED25519_SEED='a'*64)

    def test_valid(self):
        self.assertEqual(validate_environment(self.env), 'backend-elevage-pilot.onrender.com')

    def test_every_missing_setting_is_refused(self):
        for name in self.env:
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_environment({k:v for k,v in self.env.items() if k != name})

    def test_database_destinations(self):
        for url in ('sqlite:///db.sqlite3', 'postgresql://user:x@dpg-prod-a/elevage',
                    'postgresql://elevage_pilot_owner:x@dpg-synthetic123-a/elevage',
                    'postgresql://elevage_pilot_owner:x@localhost/elevage_pilot',
                    'postgresql://elevage_pilot_owner:x@dpg-synthetic123-a:123/elevage_pilot',
                    self.env['DATABASE_URL']+'?host=production', self.env['DATABASE_URL']+'#fragment',
                    'postgresql://elevage_pilot_owner@dpg-synthetic123-a/elevage_pilot'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_environment(dict(self.env, DATABASE_URL=url))

    def test_runtime_and_secrets(self):
        for name,value in (('APP_ENV','production'),('DEBUG','true'),('PILOT_WEB_HOST','backend-elevage.onrender.com'),
            ('PILOT_WEB_HOST','*'),('SECRET_KEY','short'),('PILOT_ED25519_SEED','invalid'),
            ('OFFLINE_SIGNING_PRIVATE_KEY','inherited')):
            with self.subTest(name=name), self.assertRaises(ValueError):
                validate_environment(dict(self.env, **{name:value}))


if __name__ == '__main__':
    unittest.main()
