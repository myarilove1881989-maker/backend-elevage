"""API-level evidence only: no claim of Flutter or physical-device execution."""
from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import (MembershipPermission, Achat, Vente, Depense, Client,
    TerrainSubmission, TerrainEntityMapping, CategorieDepense)
from core.offline_permissions import contract_for
from core.access_policy import effective_permissions
from core.tests.test_terrain_operations import OperationFixture


RIGHTS=['purchases.create','clients.create','animal_sales.create','expenses.create',
    'mortality.create','births.create','donations.create','thefts.create',
    'egg_collections.create','egg_sales.create','cash.create','feed.create',
    'weights.create','agenda.progress']


class FunctionalJourneyTests(OperationFixture, TestCase):
    def setUp(self):
        super().setUp()
        MembershipPermission.objects.filter(membership=self.member).delete()
        # Exercise OWNER's actual update API rather than assign implicit role rights.
        api=APIClient();api.force_authenticate(self.owner)
        response=api.patch(f'/api/memberships/{self.member.pk}/',
            {'permissions':RIGHTS,'expected_version':self.member.version},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.member.refresh_from_db()
        self.grant.rights_version=self.member.version
        self.grant.revoked_at=None
        self.grant.expires_at=self.grant.issued_at+timedelta(days=7)
        self.grant.capabilities['permission_contract']=contract_for(self.grant,effective_permissions(self.member))
        self.grant.save()

    def test_classic_routes_require_device_proof_even_with_individual_rights(self):
        api=APIClient();api.force_authenticate(self.jean)
        routes=[('achats/create/',{}),('mouvements/create/',{'type_mouvement':'NAISSANCE'}),
            ('mouvements/create/',{'type_mouvement':'VENTE'}),
            ('mouvements/create/',{'type_mouvement':'MORTALITE'}),
            ('mouvements/create/',{'type_mouvement':'DON'}),
            ('mouvements/create/',{'type_mouvement':'VOL'}),('depenses/create/',{}),
            ('clients/create/',{}),('payments/create/',{}),('oeufs/collectes/',{}),
            ('oeufs/ventes/',{}),('alimentation/distributions/',{}),('production/pesees/',{})]
        for route,payload in routes:
            with self.subTest(route=route,payload=payload):
                result=api.post('/api/'+route,payload,format='json')
                self.assertEqual(result.status_code,403,result.data)
                self.assertIn('DEVICE_PROOF',str(result.data))
        self.assertFalse(Achat.objects.exists())

    def test_online_legacy_purchase_sale_expense_mortality_and_owner_consultation(self):
        self.farm.offline_policy_enabled=False;self.farm.save()
        self.jean.refresh_from_db()
        api=APIClient();api.force_authenticate(self.jean)
        date=timezone.localdate().isoformat()
        customer=api.post('/api/clients/create/',{'nom':'Client fictif','telephone':'0000000000'},format='json')
        self.assertEqual(customer.status_code,201,customer.data)
        purchase=api.post('/api/achats/create/',{'nom_lot':'Lot en ligne','espece':self.species.pk,
            'quantite':10,'prix_unitaire':'10.00','prix_total':'100.00','date':date},format='json')
        self.assertEqual(purchase.status_code,201,purchase.data)
        lot=Achat.objects.get().lot
        sale=api.post('/api/mouvements/create/',{'lot':lot.pk,'client':customer.data['id'],
            'type_mouvement':'VENTE','quantite':2,'prix_unitaire':'15.00','date':date},format='json')
        self.assertEqual(sale.status_code,201,sale.data)
        category=CategorieDepense.objects.create(exploitation=self.farm,nom='Fictive')
        expense=api.post('/api/depenses/create/',{'lot':lot.pk,'categorie':category.pk,
            'montant':'12.34','date':date},format='json')
        self.assertEqual(expense.status_code,201,expense.data)
        mortality=api.post('/api/mouvements/create/',{'lot':lot.pk,'type_mouvement':'MORTALITE',
            'quantite':1,'date':date},format='json')
        self.assertEqual(mortality.status_code,201,mortality.data)
        self.assertEqual(lot.stock,7)
        self.assertEqual(Vente.objects.get().montant_total,Decimal('30.00'))
        self.assertEqual(Depense.objects.get().montant,Decimal('12.34'))
        self.assertEqual(Achat.objects.get().created_by,self.jean)
        api.force_authenticate(self.owner)
        for path in ('achats/','depenses/','mouvements/','clients/','history/'):
            result=api.get('/api/'+path)
            self.assertEqual(result.status_code,200,result.data)
            self.assertTrue(result.data)

    def test_expense_classic_denies_owner_missing_right_and_new_tablet_policy(self):
        self.farm.offline_policy_enabled=False;self.farm.save()
        self.jean.refresh_from_db()
        category=CategorieDepense.objects.create(exploitation=self.farm,nom='Fictive')
        data={'lot':self.lot.pk,'categorie':category.pk,'montant':'12.34',
            'date':timezone.localdate().isoformat()}
        api=APIClient();api.force_authenticate(self.owner)
        self.assertEqual(api.post('/api/depenses/create/',data,format='json').status_code,403)
        api.force_authenticate(self.jean)
        MembershipPermission.objects.filter(membership=self.member,code='expenses.create').delete()
        self.assertEqual(api.post('/api/depenses/create/',data,format='json').status_code,403)
        MembershipPermission.objects.create(membership=self.member,code='expenses.create',created_by=self.owner)
        self.farm.offline_policy_enabled=True;self.farm.save()
        self.jean.refresh_from_db()
        result=api.post('/api/depenses/create/',data,format='json')
        self.assertEqual(result.status_code,403,result.data)
        self.assertIn('DEVICE_PROOF',str(result.data))
        self.assertFalse(Depense.objects.exists())

    def test_expense_classic_precision_and_author_are_server_validated(self):
        self.farm.offline_policy_enabled=False;self.farm.save()
        self.jean.refresh_from_db()
        category=CategorieDepense.objects.create(exploitation=self.farm,nom='Fictive')
        data={'lot':self.lot.pk,'categorie':category.pk,'montant':'12.345',
            'date':timezone.localdate().isoformat()}
        api=APIClient();api.force_authenticate(self.jean)
        self.assertEqual(api.post('/api/depenses/create/',data,format='json').status_code,400)
        self.assertFalse(Depense.objects.exists())
        data['montant']='0.29'
        result=api.post('/api/depenses/create/',data,format='json')
        self.assertEqual(result.status_code,201,result.data)
        expense=Depense.objects.get()
        self.assertEqual(expense.montant,Decimal('0.29'))
        self.assertEqual(expense.created_by,self.jean)

    def test_v2_local_client_dependency_four_operations_replay_and_owner_history(self):
        operations=[]
        def submit(kind,payload,dependencies=()):
            operation=self.terrain(kind,payload,len(operations)+1,dependencies)
            self.assertEqual(self.apply(operation)['business_status'],'CONFIRMED')
            operations.append(operation)
            return operation
        with patch('django.utils.timezone.now',return_value=self.grant.issued_at+timedelta(days=6)):
            customer=submit('CLIENT',{'nom':'Client local fictif','ville':'Ville fictive'})
            purchase=submit('ACHAT',{'nom_lot':'Lot V2','espece':self.species.pk,
                'quantite':10,'prix_unitaire':'10.00','prix_total':'100.00'})
            ref={'local_uuid':purchase['local_entity_id']}
            submit('VENTE_ANIMAUX',{'lot_ref':ref,'client_ref':{'local_uuid':customer['local_entity_id']},
                'quantite':2,'prix_unitaire':'15.00'},[purchase['client_operation_id'],customer['client_operation_id']])
            submit('DEPENSE',{'lot_ref':ref,'montant':'12.34'},[purchase['client_operation_id']])
            submit('MORTALITE',{'lot_ref':ref,'quantite':1},[purchase['client_operation_id']])
            for operation in operations:
                self.assertEqual(self.apply(operation)['business_status'],'CONFIRMED')
        self.assertEqual(Achat.objects.get().lot.stock,7)
        self.assertEqual(Achat.objects.get().prix_total,Decimal('100.00'))
        self.assertEqual(Vente.objects.get().montant_total,Decimal('30.00'))
        self.assertEqual(Depense.objects.get().montant,Decimal('12.34'))
        self.assertEqual(Client.objects.get().created_by,self.jean)
        self.assertEqual(TerrainSubmission.objects.count(),5)
        self.assertEqual(TerrainSubmission.objects.filter(author_user_id=self.jean.pk).count(),5)
        self.assertEqual(TerrainEntityMapping.objects.filter(entity_type='CLIENT').count(),1)
        api=APIClient();api.force_authenticate(self.owner)
        history=api.get('/api/history/')
        self.assertEqual(history.status_code,200,history.data)
        self.assertGreaterEqual(history.data['count'],5)
        for operation in operations:
            row=TerrainSubmission.objects.get(client_operation_id=operation['client_operation_id'])
            self.assertEqual(row.payload,operation['payload'])
            self.assertEqual(row.offline_authorization_id,self.grant.pk)

    def test_no_sale_and_no_expense_right_and_withdrawal_keep_originals_without_effect(self):
        for kind,code,payload in [('DEPENSE','expenses.create',{'lot_ref':self.lot_ref(),'montant':'12.34'}),
                ('VENTE_ANIMAUX','animal_sales.create',{'lot_ref':self.lot_ref(),'client_ref':{'server_id':1},
                    'quantite':2,'prix_unitaire':'15.00'})]:
            operation=self.terrain(kind,payload,1 if kind=='DEPENSE' else 2)
            MembershipPermission.objects.filter(membership=self.member,code=code).delete()
            receipt=self.apply(operation)
            self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION',receipt)
            self.assertEqual(receipt['reason_code'],'OFFLINE_PERMISSION_DENIED')
            self.assertEqual(TerrainSubmission.objects.get(client_operation_id=operation['client_operation_id']).payload,payload)
        self.assertFalse(Depense.objects.exists());self.assertFalse(Vente.objects.exists())
