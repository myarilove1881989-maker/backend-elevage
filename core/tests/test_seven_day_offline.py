from datetime import timedelta
from unittest.mock import patch
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.exceptions import PermissionDenied, ValidationError
from core.models import (MembershipPermission, OfflineAuthorization, Client, Lot, Mouvement,
    Task, CategorieDepense, TerrainSubmission, AuditEvent, Depense, Achat, Payment)
from core.access_policy import effective_permissions, authorize_business_service
from core.offline_permissions import contract_for, permission_failure
from core.tests.test_terrain_operations import OperationFixture
from core.tests import test_offline_permissions as fine_tests
from core.terrain_application import process_pending


RIGHTS = ['purchases.create','births.create','mortality.create','donations.create',
          'thefts.create','animal_sales.create','egg_collections.create','egg_sales.create',
          'expenses.create','cash.create','feed.create','weights.create','agenda.progress']


class SevenDayOfflineTests(OperationFixture, TestCase):
    def setUp(self):
        super().setUp()
        MembershipPermission.objects.filter(membership=self.member).delete()
        MembershipPermission.objects.bulk_create([MembershipPermission(membership=self.member,
            code=code,created_by=self.owner) for code in RIGHTS])
        self.grant.expires_at=self.grant.issued_at+timedelta(days=7)
        self.grant.capabilities['permission_contract']=contract_for(self.grant,effective_permissions(self.member))
        self.grant.save()

    def test_exact_seven_day_boundary_and_no_expired_new_intention(self):
        for offset,expected in [(timedelta(seconds=-1),''),(timedelta(0),'OFFLINE_WINDOW_INVALID'),
                                (timedelta(seconds=1),'OFFLINE_WINDOW_INVALID')]:
            data=self.terrain('ACHAT',{})
            data['local_recorded_at']=(self.grant.expires_at.replace(microsecond=0)+offset).isoformat()
            from core.terrain_transport import SubmissionInput
            parsed=SubmissionInput().run_validation(data)
            with patch('django.utils.timezone.now',return_value=self.grant.expires_at+timedelta(days=1)):
                self.assertEqual(permission_failure(self.grant,parsed),expected)

    @override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True,OFFLINE_FINE_AUTHORIZATION_HOURS=168)
    def test_server_issues_seven_days_only_with_explicit_opt_in(self):
        fine_tests.OfflinePermissionTests.issue(self,2)
        latest=OfflineAuthorization.objects.exclude(pk=self.grant.pk).get()
        self.assertEqual(latest.expires_at-latest.issued_at,timedelta(days=7))
        self.assertEqual(latest.capabilities['permission_contract']['exp']-
                         latest.capabilities['permission_contract']['iat'],604800)
        for hours in (0,169,True):
            with override_settings(OFFLINE_FINE_AUTHORIZATION_HOURS=hours):
                with self.assertRaises(ValidationError):fine_tests.OfflinePermissionTests.issue(self,2)

    def test_delayed_original_after_expiry_applies_once_and_keeps_original_grant(self):
        data=self.terrain('DEPENSE',{'lot_ref':self.lot_ref(),'montant':'12.34'})
        recorded=self.grant.issued_at+timedelta(days=6)
        data['local_recorded_at']=recorded.isoformat()
        data['business_occurred_at']=recorded.isoformat()
        with patch('django.utils.timezone.now',return_value=self.grant.expires_at+timedelta(days=2)):
            self.assertEqual(self.apply(data)['business_status'],'CONFIRMED')
            self.assertEqual(self.apply(data)['business_status'],'CONFIRMED')
        self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(TerrainSubmission.objects.get().offline_authorization_id,self.grant.pk)

    def test_revoked_author_and_removed_right_preserve_originals_without_effect(self):
        data=self.terrain('DEPENSE',{'lot_ref':self.lot_ref(),'montant':'12.34'})
        with patch('core.terrain_views.process_pending'):self.apply(data)
        MembershipPermission.objects.filter(membership=self.member,code='expenses.create').delete()
        process_pending(self.device.pk)
        self.assertFalse(Depense.objects.exists())
        row=TerrainSubmission.objects.get()
        self.assertEqual(row.payload,data['payload'])
        self.assertEqual(row.outcome.reason_code,'OFFLINE_PERMISSION_DENIED')
        self.member.is_active=False;self.member.save()
        with override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True):
            with self.assertRaises(PermissionDenied):fine_tests.OfflinePermissionTests.issue(self,2)

    def test_owner_cannot_perform_any_requested_terrain_action(self):
        for action in RIGHTS:
            with self.subTest(action=action), self.assertRaises(PermissionDenied):
                authorize_business_service(self.owner,self.farm,action)

    @override_settings(OFFLINE_FINE_PERMISSIONS_ENABLED=True,OFFLINE_FINE_AUTHORIZATION_HOURS=168)
    def test_renewed_contract_uses_reduced_rights_without_changing_original_grant(self):
        before=dict(self.grant.capabilities['permission_contract'])
        MembershipPermission.objects.filter(membership=self.member,code='expenses.create').delete()
        self.member.version+=1;self.member.save()
        fine_tests.OfflinePermissionTests.issue(self,2)
        renewed=OfflineAuthorization.objects.exclude(pk=self.grant.pk).get()
        self.assertNotIn('expenses.create',renewed.capabilities['permission_contract']['permissions'])
        self.assertEqual(renewed.rights_version,self.member.version)
        self.grant.refresh_from_db()
        self.assertEqual(self.grant.capabilities['permission_contract'],before)

    def test_all_thirteen_business_operations_apply_once_under_seven_day_contract(self):
        customer=Client.objects.create(exploitation=self.farm,nom='Client fictif')
        category=CategorieDepense.objects.create(exploitation=self.farm,nom='Aliment fictif')
        eggs=Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Ponte fictive',
            date_debut=timezone.localdate(),type_production='OEUFS')
        Mouvement.objects.create(lot=eggs,exploitation=self.farm,type_mouvement='ACHAT',quantite=10)
        task=Task.objects.create(exploitation=self.farm,title='Compte rendu',date=timezone.localdate(),assigned_to=self.jean)
        with patch('django.utils.timezone.now',return_value=self.grant.issued_at+timedelta(days=6)):
            operations=[]
            def submit(kind,payload,dependencies=(),operation_type='CREATE',version=''):
                data=self.terrain(kind,payload,len(operations)+1,dependencies)
                data.update(operation_type=operation_type,expected_server_version=version)
                self.assertEqual(self.apply(data)['business_status'],'CONFIRMED',(kind,data))
                operations.append(data)
                return data
            purchase=submit('ACHAT',{'nom_lot':'Lot local','espece':self.species.pk,'quantite':10,
                'prix_total':'100.00','prix_unitaire':'10.00'})
            lot_ref={'local_uuid':purchase['local_entity_id']}; deps=[purchase['client_operation_id']]
            submit('NAISSANCE',{'lot_ref':self.lot_ref(),'total_naissances':4,'mort_nes':1,'nom_nouveau_lot':'Enfant fictif'})
            for kind in ('MORTALITE','DON','VOL'):submit(kind,{'lot_ref':lot_ref,'quantite':1},deps)
            sale=submit('VENTE_ANIMAUX',{'lot_ref':lot_ref,'client_ref':{'server_id':customer.pk},
                'quantite':2,'prix_unitaire':'15.00'},deps)
            collection=submit('COLLECTE_OEUFS',{'lot_ref':{'server_id':eggs.pk},'nombre_collecte':12,
                'nombre_casses':2,'nombre_declasses':1,'nombre_consommes_donnes':1})
            submit('VENTE_OEUFS',{'lot_ref':{'server_id':eggs.pk},'client_ref':{'server_id':customer.pk},
                'conditionnement':'UNITE','nombre_conditionnements':2,'prix_unitaire_conditionnement':'0.13'},
                [collection['client_operation_id']])
            submit('ENCAISSEMENT',{'client_ref':{'server_id':customer.pk},'vente_ref':{'local_uuid':sale['local_entity_id']},
                'montant_recu':'30.00','mode':'ESPECES'},[sale['client_operation_id']])
            expense=submit('DEPENSE',{'lot_ref':lot_ref,'categorie_id':category.pk,'montant':'12.34'},deps)
            submit('ALIMENTATION',{'lot_ref':lot_ref,'aliment':'Aliment fictif','quantite_kg':'1.000',
                'prix_kg':'12.34','depense_ref':{'local_uuid':expense['local_entity_id']}},
                [purchase['client_operation_id'],expense['client_operation_id']])
            submit('PESEE',{'lot_ref':lot_ref,'nombre_animaux_peses':2,'poids_total_kg':'3.000'},deps)
            submit('TASK',{'task_id':task.pk,'status':'IN_PROGRESS','report':'Fait sans rÃ©seau'},
                operation_type='UPDATE',version=str(task.version))
            for data in operations:self.assertEqual(self.apply(data)['business_status'],'CONFIRMED')
        self.assertEqual(len(operations),13)
        self.assertEqual(TerrainSubmission.objects.count(),13)
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_APPLIED').count(),13)
        self.assertEqual(Achat.objects.count(),1)
        self.assertEqual(Achat.objects.get().lot.stock,5)
        self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(Payment.objects.count(),1)
