from uuid import uuid4
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.tests.test_terrain_operations import OperationFixture
from core.models import CategorieDepense,Depense,MembershipPermission
from core.supply_models import SupplyProduct,SupplyReceipt,SupplyMovement,SupplyVial,SupplyVialOpening,SupplyCategoryBinding,SupplyOperation
from core.vaccination_models import Vaccination
from core.offline_permissions import contract_for
from core.access_policy import effective_permissions


class VaccineTests(OperationFixture,TestCase):
    def setUp(self):
        super().setUp()
        self.category=CategorieDepense.objects.create(exploitation=self.farm,nom='Vaccin renommable')
        SupplyCategoryBinding.objects.create(category=self.category,purpose='VACCINE')
        self.product=SupplyProduct.objects.create(exploitation=self.farm,kind='VACCINE',name='Vaccin synthétique')
        for code in ['vaccinations.create','vaccinations.update','vaccinations.delete','vaccinations.loss']:
            MembershipPermission.objects.get_or_create(membership=self.member,code=code)
        self.grant.capabilities.update(vaccine_stock_contract=1,permission_contract=contract_for(self.grant,effective_permissions(self.member)))
        self.grant.save()
        self.api=APIClient();self.api.force_authenticate(self.owner);self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
    def body(self,**extra):
        return {'operation_uuid':str(uuid4()),'lot':self.lot.pk,'categorie':self.category.pk,'montant':'1000.00','date':str(timezone.localdate()),
            'vaccine_supply':{'product':str(self.product.pk),'species':self.species.pk,'vial_count':10,'doses_per_vial':20,
            'manufacturer_batch':'LOT-SYNTHETIQUE','expires_on':str(timezone.localdate()+timedelta(days=30)),
            'received_on':str(timezone.localdate()),**extra}}
    def buy(self,**extra):
        body=self.body(**extra);response=self.api.post('/api/depenses/create/',body,format='json')
        self.assertEqual(response.status_code,201,response.data);return body
    def vials(self):return list(SupplyVial.objects.order_by('ordinal'))
    def vaccination(self,**extra):
        vials=self.vials()
        return {'operation_uuid':str(uuid4()),'lot':self.lot.pk,'product':str(self.product.pk),'animals':35,
            'vaccinated_at':timezone.now().isoformat(),'allocations':[{'vial':str(vials[0].pk),'doses':20},{'vial':str(vials[1].pk),'doses':15}],**extra}
    def stock(self):return self.api.get('/api/vaccine-supplies/').data
    def physical(self):return sum(v['physical'] for v in self.stock()['vials'])
    def test_200_then_35_single_expense_single_physical_effect_and_retries(self):
        body=self.buy();self.api.post('/api/depenses/create/',body,format='json')
        self.assertEqual(SupplyReceipt.objects.count(),1);self.assertEqual(SupplyVial.objects.count(),10)
        vaccine=self.vaccination()
        for _ in range(2):
            response=self.api.post('/api/vaccinations/',vaccine,format='json');self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(self.physical(),165);self.assertEqual(Depense.objects.count(),1);self.assertEqual(Vaccination.objects.count(),1)
        self.assertEqual(SupplyVialOpening.objects.count(),2)
        self.assertEqual([v['physical'] for v in sorted(self.stock()['vials'],key=lambda v:v['label'])].count(5),1)
    def test_expired_physical_doses_cannot_be_administered(self):
        self.buy(expires_on=str(timezone.localdate()-timedelta(days=1)))
        self.assertEqual(self.physical(),200);self.assertEqual(sum(v['usable'] for v in self.stock()['vials']),0)
        response=self.api.post('/api/vaccinations/',self.vaccination(),format='json')
        self.assertEqual(response.status_code,400);self.assertEqual(Vaccination.objects.count(),0)
    def test_partial_opening_duration_and_declared_loss(self):
        self.buy();vial=self.vials()[0]
        body=self.vaccination(animals=5,allocations=[{'vial':str(vial.pk),'doses':5,'lost':2,'usable_seconds':60}])
        self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,201)
        self.assertEqual(self.physical(),193)
        opening=SupplyVialOpening.objects.get(vial=vial);self.assertEqual(opening.usable_seconds,60)
        body=self.vaccination(animals=0,allocations=[{'vial':str(vial.pk),'doses':0,'lost':13}])
        self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,201)
        self.assertEqual(self.physical(),180)
    def test_corrections_and_cancel_preserve_ledger_and_opening(self):
        self.buy();created=self.api.post('/api/vaccinations/',self.vaccination(),format='json').data
        url=f"/api/vaccinations/{created['id']}/"
        body={'operation_uuid':str(uuid4()),'expected_version':created['version'],'reason':'Quantité vérifiée',
            'vaccinated_at':created['vaccinated_at'],'animals':10,'allocations':[{'vial':str(self.vials()[1].pk),'doses':10}]}
        first=self.api.patch(url,body,format='json');self.assertEqual(first.status_code,200,first.data)
        self.assertEqual(self.physical(),190)
        # A second correction must reverse only effective negative lines.
        body.update(operation_uuid=str(uuid4()),expected_version=first.data['version'],animals=8,
            allocations=[{'vial':str(self.vials()[1].pk),'doses':8}])
        second=self.api.patch(url,body,format='json');self.assertEqual(second.status_code,200,second.data);self.assertEqual(self.physical(),192)
        cancel={'operation_uuid':str(uuid4()),'expected_version':second.data['version'],'reason':'Annulation vérifiée','confirm':True}
        for _ in range(2):self.assertEqual(self.api.delete(url,cancel,format='json').status_code,200)
        self.assertEqual(self.physical(),200);self.assertEqual(Vaccination.objects.count(),1)
        self.assertEqual(SupplyVialOpening.objects.count(),2);self.assertTrue(self.api.get('/api/vaccinations/').data[0]['voided'])
        self.assertEqual(Depense.objects.get().montant,1000)
        history=self.api.get(url).data['history']
        self.assertEqual([version['kind'] for version in history],['VAC_ADMIN','VAC_EDIT','VAC_EDIT','VAC_CANCEL'])
        self.assertEqual(history[0]['animals'],35)
    def test_offline_vaccination_sync_and_duplicate_then_web_conflict(self):
        self.buy();body=self.vaccination();body.pop('operation_uuid');body.pop('vaccinated_at');body['lot_ref']={'server_id':body.pop('lot')};body['vaccine_version']=1
        original=self.terrain('VACCINATION',body,1)
        for _ in range(2):
            result=self.apply(original);self.assertEqual(result['business_status'],'CONFIRMED',result)
        self.assertEqual(self.physical(),165);self.assertIn('vaccine_stock_snapshot',result)
        other=self.terrain('VACCINATION',body,2)
        self.assertEqual(self.apply(other)['business_status'],'NEEDS_RECONCILIATION');self.assertEqual(self.physical(),165)
    def test_offline_purchase_and_revoke_and_closed_lot(self):
        body=self.body();p={'vaccine_version':1,'lot_ref':{'server_id':body['lot']},'categorie_id':body['categorie'],'montant':body['montant'],'vaccine_supply':body['vaccine_supply']}
        original=self.terrain('DEPENSE',p,1);result=self.apply(original);self.assertEqual(result['business_status'],'CONFIRMED',result)
        self.apply(original);self.assertEqual(self.physical(),200);self.assertEqual(Depense.objects.count(),1)
        self.grant.revoked_at=timezone.now();self.grant.save()
        body=self.vaccination();body.pop('operation_uuid');body.pop('vaccinated_at');body['lot_ref']={'server_id':body.pop('lot')};body['vaccine_version']=1
        self.assertEqual(self.apply(self.terrain('VACCINATION',body,2))['business_status'],'NEEDS_RECONCILIATION')
        self.lot.date_fin=timezone.localdate();self.lot.save()
        self.assertEqual(self.api.post('/api/vaccinations/',self.vaccination(),format='json').status_code,400)
        self.assertEqual(self.physical(),200)
    def test_cross_farm_and_assignment_and_insufficient_refused(self):
        self.buy(lot=self.lot.pk)
        body=self.vaccination(product=str(uuid4()))
        self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,400)
        body=self.vaccination(animals=21,allocations=[{'vial':str(self.vials()[0].pk),'doses':21}])
        self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,400);self.assertEqual(self.physical(),200)
    def test_real_foreign_farm_cannot_read_or_consume_another_farms_vial(self):
        from core.models import User,Espece,Lot
        self.buy();original=self.vaccination()
        foreign=User.objects.create_user(username='foreign-vaccine-owner',email='foreign-vaccine@example.invalid',password='synthetic-test-password')
        species=Espece.objects.create(exploitation=foreign.exploitation,nom='Foreign')
        lot=Lot.objects.create(exploitation=foreign.exploitation,espece=species,nom='Foreign lot',date_debut=timezone.localdate())
        self.api.force_authenticate(foreign)
        self.assertEqual(self.api.get('/api/vaccine-supplies/').data['vials'],[])
        response=self.api.post('/api/vaccinations/',{**original,'lot':lot.pk},format='json')
        self.assertEqual(response.status_code,400);self.assertEqual(Vaccination.objects.count(),0)
        self.api.force_authenticate(self.owner);self.assertEqual(self.physical(),200)

    def test_open_duration_expiry_is_excluded_without_destroying_physical_doses(self):
        from unittest.mock import patch
        self.buy();vial=self.vials()[0];at=timezone.now()
        body=self.vaccination(animals=5,vaccinated_at=at.isoformat(),allocations=[{'vial':str(vial.pk),'doses':5,'usable_seconds':60}])
        self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,201)
        with patch('core.vaccine_views.timezone.now',return_value=at+timedelta(seconds=61)):
            snapshot=self.stock();self.assertEqual(sum(v['usable'] for v in snapshot['vials']),180)
            self.assertEqual(sum(v['physical'] for v in snapshot['vials']),195)

    def test_signed_loss_permission_and_feature_are_required(self):
        self.buy();p=self.vaccination(animals=0,allocations=[{'vial':str(self.vials()[0].pk),'doses':0,'lost':1}])
        p.pop('operation_uuid');p.pop('vaccinated_at');p['lot_ref']={'server_id':p.pop('lot')};p['vaccine_version']=1
        MembershipPermission.objects.filter(membership=self.member,code='vaccinations.loss').delete()
        result=self.apply(self.terrain('VACCINATION',p,1));self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION');self.assertEqual(self.physical(),200)
        self.grant.capabilities.pop('vaccine_stock_contract');self.grant.save()
        p['animals']=1;p['allocations']=[{'vial':str(self.vials()[0].pk),'doses':1}]
        self.assertEqual(self.apply(self.terrain('VACCINATION',p,2))['business_status'],'NEEDS_RECONCILIATION')

    def test_reserved_vial_and_other_species_are_not_shared_implicitly(self):
        from core.models import Lot,Espece
        self.buy(lot=self.lot.pk)
        other=Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Other',date_debut=timezone.localdate())
        response=self.api.post('/api/vaccinations/',self.vaccination(lot=other.pk),format='json');self.assertEqual(response.status_code,400)
        species=Espece.objects.create(exploitation=self.farm,nom='Other species')
        other=Lot.objects.create(exploitation=self.farm,espece=species,nom='Other species lot',date_debut=timezone.localdate())
        self.assertEqual(self.api.post('/api/vaccinations/',self.vaccination(lot=other.pk),format='json').status_code,400)
        self.assertEqual(self.physical(),200)
    def test_web_and_signed_device_conflict_is_visible_and_only_classified_without_effect(self):
        from core.models import TerrainSubmission
        self.buy();body=self.vaccination();self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,201)
        body.pop('operation_uuid');body.pop('vaccinated_at');body['lot_ref']={'server_id':body.pop('lot')};body['vaccine_version']=1
        original=self.terrain('VACCINATION',body,1);result=self.apply(original)
        self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION');self.assertEqual(self.physical(),165)
        decision={'decision_uuid':str(uuid4()),'expected_decision_version':0,'action':'CANCEL','reason':'Stock déjà utilisé sur autre appareil'}
        url=f"/api/offline/reconciliation/{original['client_operation_id']}/"
        self.assertEqual(self.api.post(url,{**decision,'action':'APPLY_ORIGINAL'},format='json').status_code,403)
        response=self.api.post(url,decision,format='json');self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['receipt']['business_status'],'NOT_APPLIED');self.assertEqual(self.physical(),165)
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=original['client_operation_id']).payload,original['payload'])

    def test_signed_correction_and_cancellation_use_original_permissions_and_versions(self):
        self.buy();p=self.vaccination();p.pop('operation_uuid');p.pop('vaccinated_at');p['lot_ref']={'server_id':p.pop('lot')};p['vaccine_version']=1
        original=self.terrain('VACCINATION',p,1);result=self.apply(original);self.assertEqual(result['business_status'],'CONFIRMED')
        item=result['vaccination_snapshot']
        edit=self.terrain('VACCINATION',{'vaccine_version':1,'vaccination_ref':{'server_id':item['id']},'animals':10,
            'allocations':[{'vial':str(self.vials()[1].pk),'doses':10}],'vaccinated_at':item['vaccinated_at'],'reason':'Quantité vérifiée'},2)
        edit.update(operation_type='UPDATE',expected_server_version=item['version'])
        result=self.apply(edit);self.assertEqual(result['business_status'],'CONFIRMED',result);self.assertEqual(self.physical(),190)
        cancel=self.terrain('VACCINATION',{'vaccine_version':1,'vaccination_ref':{'server_id':item['id']},'reason':'Annulation vérifiée'},3)
        cancel.update(operation_type='REVERSE',expected_server_version=result['vaccination_snapshot']['version'])
        result=self.apply(cancel);self.assertEqual(result['business_status'],'CONFIRMED',result);self.assertEqual(self.physical(),200)
        self.apply(cancel);self.assertEqual(self.physical(),200);self.assertEqual(SupplyVialOpening.objects.count(),2)
    def test_partial_loss_requires_explicit_opening_and_full_destroyed_vial_does_not_invent_opening(self):
        self.buy();vial=self.vials()[0]
        body=self.vaccination(animals=0,allocations=[{'vial':str(vial.pk),'doses':0,'lost':5}])
        response=self.api.post('/api/vaccinations/',body,format='json');self.assertEqual(response.status_code,400)
        self.assertEqual(self.physical(),200);self.assertEqual(SupplyVialOpening.objects.count(),0)
        body['allocations'][0]['opened_at']=timezone.now().isoformat()
        body['vaccinated_at']=timezone.now().isoformat()
        response=self.api.post('/api/vaccinations/',body,format='json');self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(self.physical(),195);self.assertIsNone(SupplyVialOpening.objects.get().usable_seconds)
        other=self.vials()[1]
        body=self.vaccination(animals=0,allocations=[{'vial':str(other.pk),'doses':0,'lost':20}])
        self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,201)
        self.assertEqual(self.physical(),175);self.assertEqual(SupplyVialOpening.objects.count(),1)
    def test_new_vaccine_reference_is_atomic_stable_and_category_rename_does_not_change_its_purpose(self):
        identifier=uuid4();body=self.body();body['vaccine_supply'].pop('product')
        body['vaccine_supply']['new_product']={'id':str(identifier),'name':'Nouveau vaccin synthétique'}
        self.category.nom='Approvisionnement santé';self.category.save()
        response=self.api.post('/api/depenses/create/',{**body,'montant':'invalide'},format='json')
        self.assertEqual(response.status_code,400);self.assertFalse(SupplyProduct.objects.filter(pk=identifier).exists())
        self.assertEqual(Depense.objects.count(),0)
        for _ in range(2):
            response=self.api.post('/api/depenses/create/',body,format='json');self.assertEqual(response.status_code,201,response.data)
        self.assertEqual(SupplyVial.objects.count(),10);self.assertEqual(SupplyReceipt.objects.count(),1)
        self.assertEqual(str(SupplyReceipt.objects.get().product_id),str(identifier));self.assertEqual(self.physical(),200)
