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

    def test_phase2a_to_phase2b_preserves_populated_aquatic_tables(self):
        import uuid
        from django.utils import timezone
        targets=[('core','0052_remove_membershippermission_membership_action_known_and_more'),
            ('pisciculture','0005_remove_observation_aquatic_observation_values_and_more')]
        executor=MigrationExecutor(connection);executor.migrate(targets)
        apps=executor.loader.project_state(targets).apps
        user=User.objects.get(username='aquatic-upgrade-synthetic')
        species=apps.get_model('core','Espece').objects.create(exploitation_id=user.exploitation_id,nom='Tilapia')
        site=apps.get_model('pisciculture','Installation').objects.create(exploitation_id=user.exploitation_id,code='PRESERVE',name='Preserve',type='BASSIN')
        cohort=apps.get_model('pisciculture','Cohort').objects.create(exploitation_id=user.exploitation_id,code='PRESERVE',
            name='Preserve',species_id=species.pk,origin={'type':'OTHER'},started_at=timezone.now())
        position=apps.get_model('pisciculture','Position').objects.create(exploitation_id=user.exploitation_id,
            installation_id=site.pk,cohort_id=cohort.pk,gross_count=600)
        event=apps.get_model('pisciculture','Event').objects.create(exploitation_id=user.exploitation_id,
            cohort_id=cohort.pk,command_uuid=uuid.uuid4(),type='INITIAL',occurred_at=timezone.now(),
            actor_id=user.pk,reason='Preserved initial')
        apps.get_model('pisciculture','CountLine').objects.create(event_id=event.pk,position_id=position.pk,delta_count=600,line_no=0)
        apps.get_model('pisciculture','Observation').objects.create(exploitation_id=user.exploitation_id,
            actor_id=user.pk,operation_uuid=uuid.uuid4(),kind='WEIGHT',cohort_id=cohort.pk,
            occurred_at=timezone.now(),sample_count=50,mass_kg='12.5',result={'snapshot':'phase2a'})
        names=['pisciculture_cohort','pisciculture_position','pisciculture_event','pisciculture_countline','pisciculture_observation']
        def aquatic_rows():
            with connection.cursor() as cursor:
                result={}
                for table in names:
                    cursor.execute('SELECT * FROM '+table+' ORDER BY id');result[table]=cursor.fetchall()
                return result
        before=aquatic_rows()
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'','ELEVAGE_AQUATIC_BACKUP_SHA256':''}):
            with self.assertRaisesRegex(RuntimeError,'BACKUP_REQUIRED'):upgrade(connection)
        self.assertNotIn('pisciculture_stockfact',connection.introspection.table_names())
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'true','ELEVAGE_AQUATIC_BACKUP_SHA256':'a'*64}):
            upgrade(connection)
        self.assertEqual(aquatic_rows(),before)
        self.assertIn('pisciculture_stockfact',connection.introspection.table_names())
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'false'}):upgrade(connection)

    def test_phase2b_to_phase2c_preserves_shared_ledger_expenses_and_populations(self):
        import uuid
        from django.utils import timezone
        targets=[('core','0053_remove_membershippermission_membership_action_known_and_more'),('pisciculture','0007_stockfact_immutable')]
        executor=MigrationExecutor(connection);executor.migrate(targets)
        apps=executor.loader.project_state(targets).apps
        user=User.objects.get(username='aquatic-upgrade-synthetic');farm=user.exploitation_id
        species=apps.get_model('core','Espece').objects.create(exploitation_id=farm,nom='Existing species')
        lot=apps.get_model('core','Lot').objects.create(exploitation_id=farm,espece_id=species.pk,nom='Existing lot',date_debut=timezone.now().date())
        category=apps.get_model('core','CategorieDepense').objects.create(exploitation_id=farm,nom='Existing food expense')
        expense=apps.get_model('core','Depense').objects.create(lot_id=lot.pk,categorie_id=category.pk,montant='75.25')
        product=apps.get_model('core','SupplyProduct').objects.create(exploitation_id=farm,kind='FEED',name='Existing food')
        operation=apps.get_model('core','SupplyOperation').objects.create(exploitation_id=farm,author_id=user.pk,operation_uuid=uuid.uuid4(),kind='FEED_RECEIPT',payload={},fingerprint='a'*64,occurred_at=timezone.now())
        apps.get_model('core','SupplyReceipt').objects.create(operation_id=operation.pk,product_id=product.pk,expense_id=expense.pk,quantity='100')
        apps.get_model('core','SupplyMovement').objects.create(operation_id=operation.pk,line=0,product_id=product.pk,species_id=species.pk,lot_id=lot.pk,kind='ENTRY',quantity='100')
        site=apps.get_model('pisciculture','Installation').objects.create(exploitation_id=farm,code='OLD',name='Existing basin',type='BASSIN')
        cohort=apps.get_model('pisciculture','Cohort').objects.create(exploitation_id=farm,code='OLD',name='Existing cohort',species_id=species.pk,origin={'type':'OTHER'},started_at=timezone.now())
        apps.get_model('pisciculture','Position').objects.create(exploitation_id=farm,installation_id=site.pk,cohort_id=cohort.pk,gross_count=600)
        before=historical_snapshot(connection)
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM pisciculture_position ORDER BY id');positions=cursor.fetchall()
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'','ELEVAGE_AQUATIC_BACKUP_SHA256':''}):
            with self.assertRaisesRegex(RuntimeError,'BACKUP_REQUIRED'):upgrade(connection)
        self.assertNotIn('pisciculture_feeddistribution',connection.introspection.table_names())
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'true','ELEVAGE_AQUATIC_BACKUP_SHA256':'a'*64}):upgrade(connection)
        self.assertEqual(historical_snapshot(connection,before),before)
        with connection.cursor() as cursor:
            cursor.execute('SELECT * FROM pisciculture_position ORDER BY id');self.assertEqual(cursor.fetchall(),positions)
            constraints=connection.introspection.get_constraints(cursor,'core_depense')
        self.assertIn('core_depense_void_consistent',constraints)
        self.assertIn('expense_legacy_or_shared_parent',constraints)
        from core.models import Depense
        from core.supply_models import SupplyMovement
        self.assertEqual(Depense.objects.get(pk=expense.pk).lot_id,lot.pk)
        self.assertEqual(SupplyMovement.objects.get(operation_id=operation.pk).species_id,species.pk)
        with patch.dict(os.environ,{'ELEVAGE_ALLOW_AQUATIC_UPGRADE':'false'}):upgrade(connection)
