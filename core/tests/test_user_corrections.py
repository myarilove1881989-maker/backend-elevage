from uuid import uuid4
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.tests.test_health_agenda import HealthAgendaTests
from core.health_models import HealthResponsible,HealthTask
from core.models import User,Task,Depense,AuditEvent,Mouvement,Achat
from core.audit import audit_scope
from core.request_context import online_context
from core.serializers import LotDetailSerializer


class UserCorrectionsTests(HealthAgendaTests):
    def test_external_contact_no_login_immediate_multidate_and_idempotence(self):
        users=User.objects.count();body={'operation_uuid':str(uuid4()),'name':'Dr Synthétique','profession':'VETERINAIRE'}
        first=self.api.post('/api/tasks/health-responsibles/',body,format='json')
        self.assertEqual(first.status_code,201,first.data)
        again=self.api.post('/api/tasks/health-responsibles/',body,format='json')
        self.assertEqual(again.data['id'],first.data['id']);self.assertEqual(User.objects.count(),users)
        self.assertEqual(HealthResponsible.objects.count(),1)
        self.assertEqual(self.api.post('/api/tasks/health-responsibles/',{**body,'name':'Autre'},format='json').status_code,400)
        from datetime import timedelta
        plan={'operation_uuid':str(uuid4()),'product':str(self.product.pk),'lot':self.lot.pk,'species':self.species.pk,
            'dates':[str(timezone.localdate()+timedelta(days=i)) for i in (1,3)],'responsible_id':first.data['id'],'planned_time':None}
        before=self.physical();result=self.api.post('/api/tasks/health-plan/',plan,format='json')
        self.assertEqual(result.status_code,201,result.data);self.assertEqual(len(result.data),2)
        self.assertTrue(all(row['assigned_to'] is None and row['health']['responsible']['name']=='Dr Synthétique' for row in result.data))
        task=result.data[0]
        moved=self.api.post(f"/api/tasks/{task['id']}/health-move/",{'operation_uuid':str(uuid4()),'expected_version':task['version'],'date':str(timezone.localdate()+timedelta(days=5)),'reason':'Décalage explicite'},format='json')
        self.assertEqual(moved.status_code,200,moved.data)
        self.assertEqual(moved.data['health']['responsible']['id'],first.data['id']);self.assertIsNone(moved.data['assigned_to'])

        self.api.post('/api/tasks/health-plan/',plan,format='json');self.assertEqual(Task.objects.count(),2);self.assertEqual(self.physical(),before)

    def test_contact_and_plan_tenant_isolation(self):
        body={'operation_uuid':str(uuid4()),'name':'Dr Démo','profession':'OPERATEUR'}
        response=self.api.post('/api/tasks/health-responsibles/',body,format='json');self.assertEqual(response.status_code,201)
        self.assertEqual(self.api.post('/api/tasks/health-plan/',{'operation_uuid':str(uuid4()),'product':str(self.product.pk),'lot':self.lot.pk,'species':self.species.pk,'date':str(timezone.localdate()),'responsible_id':response.data['id']+1000},format='json').status_code,400)
        foreign_owner=User.objects.create_user(username='other-farm-owner')
        foreign=HealthResponsible.objects.create(exploitation=foreign_owner.exploitation,created_by=foreign_owner,operation_uuid=uuid4(),name='Autre ferme',profession='VETERINAIRE')
        self.assertNotIn(foreign.pk,[row['id'] for row in self.api.get('/api/tasks/health-responsibles/').data])
        rejected=self.api.post('/api/tasks/health-plan/',{'operation_uuid':str(uuid4()),'product':str(self.product.pk),'lot':self.lot.pk,'species':self.species.pk,'date':str(timezone.localdate()),'responsible_id':foreign.pk},format='json')
        self.assertEqual(rejected.status_code,400);self.assertEqual(Task.objects.count(),0)
        self.api.force_authenticate(self.jean)
        self.assertEqual(self.api.post('/api/tasks/health-responsibles/',{**body,'operation_uuid':str(uuid4())},format='json').status_code,403)

    def test_owner_online_history_has_real_author_and_no_other_farm(self):
        body={'lot':self.lot.pk,'categorie':self.category.pk,'montant':'170000.50','note':'Historique propriétaire','date':str(timezone.localdate())}
        # Use an ordinary category, not a bound supply purchase.
        from core.models import CategorieDepense
        category=CategorieDepense.objects.create(exploitation=self.farm,nom='Transport test')
        body['categorie']=category.pk
        response=self.api.post('/api/depenses/create/',body,format='json');self.assertEqual(response.status_code,201,response.data)
        history=self.api.get('/api/history/',{'view':'operations','author':self.owner.username,'kind':'DEPENSE','lot':str(self.lot.pk)})
        self.assertEqual(history.status_code,200,history.data)
        rows=history.data['results'];self.assertEqual(len(rows),1,rows)
        self.assertEqual(rows[0]['actor_user_id'],self.owner.pk);self.assertEqual(rows[0]['author_name'],self.owner.username)
        self.assertEqual(Decimal(rows[0]['payload']['montant']),Decimal('170000.50'))
        self.assertEqual(self.api.get('/api/history/',{'view':'operations','author':'999999'}).data['results'],[])
        self.api.force_authenticate(self.jean)
        self.assertEqual(self.api.get('/api/history/').status_code,200)

    def test_origin_price_purchase_birth_and_mixed_origins(self):
        self.assertIsNone(LotDetailSerializer(self.lot).data['prix_unitaire_origine'])
        purchase=Achat.objects.create(lot=self.lot,exploitation=self.farm,quantite=20,prix_total=Decimal('20000'),prix_unitaire=Decimal('1000'),date=timezone.localdate())
        self.assertEqual(LotDetailSerializer(self.lot).data['prix_unitaire_origine'],'1000.00')
        from core.models import Lot
        child=Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Nouveau né',date_debut=timezone.localdate())
        Mouvement.objects.create(lot=child,lot_origine=self.lot,exploitation=self.farm,type_mouvement='NAISSANCE',quantite=9,date=timezone.localdate())
        self.assertEqual(LotDetailSerializer(child).data['prix_unitaire_origine'],'0.00')
        Mouvement.objects.create(lot=child,exploitation=self.farm,type_mouvement='ACHAT',quantite=1,date=timezone.localdate())
        self.assertIsNone(LotDetailSerializer(child).data['prix_unitaire_origine'])


from django.test import TransactionTestCase
from django.db import connection
from django.core.management import call_command
from unittest.mock import patch
from unittest import skipUnless
from core.tests.test_terrain_operations import OperationFixture


@skipUnless(connection.vendor=='postgresql','Migration preservation requires PostgreSQL')
class HealthMigrationPreservation(OperationFixture,TransactionTestCase):
    def test_reviewed_upgrade_refuses_missing_backup_then_preserves_all_old_columns(self):
        import hashlib
        from core.production_upgrade import historical_snapshot
        from core.health_responsible_upgrade import upgrade_responsibles
        # Synthetic database only: current fixtures have real pre-existing rows.
        call_command('migrate','core','0049_health_fact_history',verbosity=0,interactive=False)
        baseline=historical_snapshot(connection)
        try:
            with patch.dict('os.environ',{'ELEVAGE_ALLOW_HEALTH_UPGRADE':'','ELEVAGE_HEALTH_BACKUP_SHA256':''}):
                with self.assertRaisesRegex(RuntimeError,'BACKUP_REQUIRED'):upgrade_responsibles(connection)
            self.assertEqual(historical_snapshot(connection,baseline),baseline)
            with patch.dict('os.environ',{'ELEVAGE_ALLOW_HEALTH_UPGRADE':'true','ELEVAGE_HEALTH_BACKUP_SHA256':hashlib.sha256(b'synthetic-local-rehearsal-only').hexdigest()}):
                upgrade_responsibles(connection)
            self.assertEqual(historical_snapshot(connection,baseline),baseline)
            self.assertEqual(HealthResponsible.objects.count(),0)
            upgrade_responsibles(connection)
        finally:
            call_command('migrate','core','0050_health_responsibles',verbosity=0,interactive=False)
