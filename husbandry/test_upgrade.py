import hashlib,json,os,subprocess
from pathlib import Path
from unittest.mock import patch
from django.test import TransactionTestCase
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from core.models import User,Espece,Lot
from .upgrade import upgrade,historical

class UpgradeQualification(TransactionTestCase):
    def test_guarded_additive_upgrade_preserves_seeded_history(self):
        self.assertEqual(connection.vendor,'postgresql')
        executor=MigrationExecutor(connection)
        executor.migrate([('husbandry',None)])
        try:
            owner=User.objects.create_user(username='synthetic-migration-owner')
            species=Espece.objects.create(exploitation=owner.exploitation,nom='Porc')
            Lot.objects.create(exploitation=owner.exploitation,espece=species,nom='Historical fixture',type_production='REPRODUCTION',date_debut='2026-01-01')
            before=historical(connection)
            q=Path(__file__).resolve().parents[2]/'qualification'
            pg=Path('C:/Program Files/PostgreSQL/18/bin')
            dump=q/'synthetic-before-husbandry.dump'
            cfg=connection.settings_dict
            env={**os.environ,'PGPASSWORD':cfg['PASSWORD'],'TEMP':str(q/'tmp'),'TMP':str(q/'tmp')}
            args=['-h',cfg['HOST'],'-p',str(cfg['PORT']),'-U',cfg['USER'],'-d',cfg['NAME']]
            subprocess.run([str(pg/'pg_dump.exe'),*args,'--format=custom','--file',str(dump)],env=env,check=True,capture_output=True)
            listed=subprocess.run([str(pg/'pg_restore.exe'),'--list',str(dump)],env=env,check=True,capture_output=True)
            self.assertIn(b'django_migrations',listed.stdout)
            digest=hashlib.sha256(dump.read_bytes()).hexdigest()
            with connection.cursor() as c:
                c.execute('SELECT count(*) FROM django_migrations');self.assertEqual(c.fetchone()[0],104)
            with patch.dict(os.environ,{'ELEVAGE_ALLOW_HUSBANDRY_UPGRADE':'','ELEVAGE_HUSBANDRY_BACKUP_SHA256':''}):
                with self.assertRaisesRegex(RuntimeError,'EXPLICIT_HUSBANDRY_UPGRADE'):upgrade(connection)
            self.assertEqual(historical(connection,before),before)
            with patch.dict(os.environ,{'ELEVAGE_ALLOW_HUSBANDRY_UPGRADE':'true','ELEVAGE_HUSBANDRY_BACKUP_SHA256':digest}):
                with patch('husbandry.upgrade.historical',side_effect=[before,{}]):
                    with self.assertRaisesRegex(RuntimeError,'HISTORICAL_DATA_CHANGED'):upgrade(connection)
                self.assertFalse(any(n.startswith('husbandry_') for n in connection.introspection.table_names()))
                with connection.cursor() as c:
                    c.execute('SELECT count(*) FROM django_migrations');self.assertEqual(c.fetchone()[0],104)
                upgrade(connection)
            self.assertEqual(historical(connection,before),before)
            with connection.cursor() as c:
                c.execute('SELECT count(*) FROM django_migrations');self.assertEqual(c.fetchone()[0],106)
            with patch.dict(os.environ,{'ELEVAGE_ALLOW_HUSBANDRY_UPGRADE':'','ELEVAGE_HUSBANDRY_BACKUP_SHA256':''}):upgrade(connection)
            result={'synthetic_only':True,'registry_before':104,'registry_after':106,'backup_sha256':digest,'backup_bytes':dump.stat().st_size,'pg_restore_list_readable':True,'historical_tables':len(before),'historical_rows':sum(x['rows'] for x in before.values()),'preserved':True,'guard_refusal':True,'repeat_start_noop':True,'atomic_failure_rolls_back':True}
            (q/'migration-qualification.json').write_text(json.dumps(result,indent=2))
        finally:
            executor=MigrationExecutor(connection);executor.migrate(executor.loader.graph.leaf_nodes())
