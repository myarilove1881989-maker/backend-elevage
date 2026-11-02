from datetime import date
from decimal import Decimal
from django.test import TestCase
from rest_framework.test import APIClient
from core.models import (User,Lot,Espece,Achat,Mouvement,Depense,CategorieDepense,
    PeseeProduction,CollecteOeufs,VenteOeufs,AuditEvent,Lettrage,Payment)
from core.tests.functional_fixtures import operator_for_tests
from core.audit import ActorContext,audit_scope

class OwnerHttpMaintenanceTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='synthetic-maint-owner')
        self.operator=operator_for_tests(self.owner,'synthetic-maint-operator',['weights.create','egg_collections.create','egg_sales.create'])
        self.farm=self.owner.exploitation
        self.client=APIClient();self.client.force_authenticate(self.owner)
        self.species=Espece.objects.create(nom='Poulet',exploitation=self.farm)
        self.lot=Lot.objects.create(nom='Lot test',exploitation=self.farm,espece=self.species,date_debut=date(2026,1,1))
        Mouvement.objects.create(lot=self.lot,exploitation=self.farm,type_mouvement='ACHAT',quantite=100,date=date(2026,1,1))
    def optin(self):self.client.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
    def body(self,values=None):
        self.farm.refresh_from_db()
        return {'farm':self.farm.pk,'values':values or {},'confirm':True,'reason':'Correction synthétique autorisée','expected_revision':self.farm.business_revision}
    def test_old_owner_contract_remains_read_only_new_owner_explicitly_writes(self):
        old=self.client.get('/api/me/capabilities/').data['permission_contract']
        self.assertEqual(old['version'],1);self.assertNotIn('weights.create',old['permissions'])
        payload={'lot':self.lot.pk,'nombre_animaux_peses':10,'poids_total_kg':'10.000'}
        self.assertEqual(self.client.post('/api/production/pesees/',payload,format='json').status_code,403)
        self.optin();new=self.client.get('/api/me/capabilities/').data['permission_contract']
        self.assertEqual(new['version'],2);self.assertIn('weights.create',new['permissions'])
        response=self.client.post('/api/production/pesees/',payload,format='json')
        self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(PeseeProduction.objects.get().created_by_id,self.owner.pk)
    def test_lot_create_update_delete_audits_and_prevents_cross_farm_species(self):
        self.optin();payload={'nom':'Vide','espece':self.species.pk,'date_debut':'2026-01-01','type_production':'CHAIR'}
        result=self.client.post('/api/maintenance/lots/',self.body(payload),format='json')
        self.assertEqual(result.status_code,201,result.data);pk=result.data['id']
        result=self.client.patch(f'/api/maintenance/lots/{pk}/',self.body({'nom':'Renommé'}),format='json')
        self.assertEqual(result.status_code,200,result.data)
        result=self.client.delete(f'/api/maintenance/lots/{pk}/',self.body(),format='json')
        self.assertEqual(result.status_code,200,result.data);self.assertFalse(Lot.objects.filter(pk=pk).exists())
        self.assertTrue(AuditEvent.objects.filter(entity_type='core.lot',entity_id=str(pk),action='DELETE').exists())
        other=User.objects.create_user(username='synthetic-other')
        foreign=Espece.objects.create(nom='Autre',exploitation=other.exploitation)
        payload['espece']=foreign.pk
        self.assertEqual(self.client.post('/api/maintenance/lots/',self.body(payload),format='json').status_code,400)
    def test_other_farm_and_operator_cannot_use_owner_maintenance(self):
        self.optin();other=User.objects.create_user(username='synthetic-other')
        foreign=Lot.objects.create(nom='Interdit',exploitation=other.exploitation,espece=self.species,date_debut=date(2026,1,1))
        self.assertEqual(self.client.get(f'/api/maintenance/lots/{foreign.pk}/').status_code,404)
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.get(f'/api/maintenance/lots/{self.lot.pk}/').status_code,403)
        contract=self.client.get('/api/me/capabilities/').data['permission_contract']
        self.assertNotIn('lots.delete',contract['permissions']);self.assertNotIn('users.manage',contract['permissions'])
    def test_weight_update_delete_recalculates_without_stock_change(self):
        self.optin();weight=PeseeProduction.objects.create(lot=self.lot,exploitation=self.farm,nombre_animaux_peses=10,poids_total_kg=10)
        result=self.client.patch(f'/api/maintenance/weights/{weight.pk}/',self.body({'poids_total_kg':'15.000'}),format='json')
        self.assertEqual(result.status_code,200,result.data)
        summary=self.client.get(f'/api/production/pesees/?lot={self.lot.pk}').data
        self.assertEqual(summary['dernier_poids_moyen_kg'],Decimal('1.500'));self.assertEqual(summary['effectif_actuel'],100)
        result=self.client.delete(f'/api/maintenance/weights/{weight.pk}/',self.body(),format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(self.client.get(f'/api/production/pesees/?lot={self.lot.pk}').data['pesees'],[])
    def test_confirmation_revision_and_signed_facts_are_protected(self):
        import uuid
        self.optin();weight=PeseeProduction.objects.create(lot=self.lot,exploitation=self.farm,nombre_animaux_peses=10,poids_total_kg=10)
        url=f'/api/maintenance/weights/{weight.pk}/'
        data=self.body();data['confirm']=False
        self.assertEqual(self.client.delete(url,data,format='json').status_code,400)
        data=self.body();data['expected_revision']=-1
        self.assertEqual(self.client.delete(url,data,format='json').status_code,400)
        with audit_scope(ActorContext(self.owner.pk,self.farm.pk,source='OFFLINE',operation_id=uuid.uuid4())):
            weight.note='signed fact';weight.save()
        self.assertEqual(self.client.patch(url,self.body({'poids_total_kg':'20'}),format='json').status_code,400)
        self.assertTrue(PeseeProduction.objects.filter(pk=weight.pk).exists())
    def test_disabled_owner_is_refused_without_mutation(self):
        self.optin();self.owner.is_active=False;self.owner.save()
        result=self.client.post('/api/maintenance/lots/',self.body({'nom':'Interdit'}),format='json')
        self.assertEqual(result.status_code,403)

    def test_owner_online_under_offline_policy_and_operator_device_rule(self):
        self.farm.offline_policy_enabled=True;self.farm.save(update_fields=['offline_policy_enabled'])
        self.owner.refresh_from_db();self.optin()
        payload={'lot':self.lot.pk,'nombre_animaux_peses':10,'poids_total_kg':'10.000'}
        self.assertEqual(self.client.post('/api/production/pesees/',payload,format='json').status_code,201)
        self.client.force_authenticate(self.operator)
        response=self.client.post('/api/production/pesees/',payload,format='json')
        self.assertEqual(response.status_code,403)
        self.assertEqual(PeseeProduction.objects.count(),1)

    def test_foreign_records_refuse_all_maintenance_verbs_and_create(self):
        self.optin();other=User.objects.create_user(username='synthetic-foreign-writes')
        species=Espece.objects.create(nom='Autre',exploitation=other.exploitation)
        lot=Lot.objects.create(nom='Autre',exploitation=other.exploitation,espece=species,date_debut=date(2026,1,1))
        weight=PeseeProduction.objects.create(lot=lot,exploitation=other.exploitation,nombre_animaux_peses=1,poids_total_kg=1)
        for method in ('patch','delete'):
            result=getattr(self.client,method)(f'/api/maintenance/weights/{weight.pk}/',self.body({'poids_total_kg':'9'}),format='json')
            self.assertEqual(result.status_code,404,result.data)
        result=self.client.post('/api/production/pesees/',{'lot':lot.pk,'nombre_animaux_peses':1,'poids_total_kg':'2'},format='json')
        self.assertEqual(result.status_code,404,result.data)
        weight.refresh_from_db();self.assertEqual(weight.poids_total_kg,1)
