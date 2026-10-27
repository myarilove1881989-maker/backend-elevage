import copy
from types import SimpleNamespace
from decimal import Decimal
from django.test import TestCase, TransactionTestCase
from django.db import transaction, IntegrityError, connection
from django.apps import apps
from django.core.exceptions import ValidationError as ModelValidationError
from rest_framework.test import APIClient
from rest_framework.exceptions import PermissionDenied
from core.models import (User, ExploitationMembership, MembershipPermission, Espece, Lot,
                         Task, AuditEvent, TerrainStockAdjustment, OfflineAuthorization)
from core.access_policy import (authorize, permission_contract, authorize_prepared_offline,
                                authorize_business_service)
from core.permission_catalog import operation_action
from core.owner_diagnostics import owner_inconsistencies


class PermissionFoundationsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner-foundations', password='Synthetic-only-password-827!')
        self.farm = self.owner.exploitation
        self.jean = User.objects.create_user(username='jean-foundations', exploitation=self.farm,
                                            password='Synthetic-only-password-827!')
        self.paul = User.objects.create_user(username='paul-foundations', exploitation=self.farm)
        self.other = User.objects.create_user(username='other-foundations')
        self.species = Espece.objects.create(nom='Poules', exploitation=self.farm)
        self.client = APIClient()

    def grant(self, user, *codes):
        member = user.memberships.get(exploitation=self.farm)
        for code in codes:
            MembershipPermission.objects.create(membership=member, code=code, created_by=self.owner)
        return member

    def test_owner_all_mutations_denied_with_both_policies(self):
        paths = ['achats/create/','achats/delete/1/','depenses/create/','depenses/delete/1/',
            'mouvements/create/','payments/create/','clients/create/','especes/',
            'oeufs/collectes/','oeufs/ventes/','oeufs/alimentation/',
            'alimentation/distributions/','production/pesees/']
        self.client.force_authenticate(self.owner)
        initial = AuditEvent.objects.count()
        for policy in (False, True):
            self.farm.offline_policy_enabled = policy
            self.farm.save(update_fields=['offline_policy_enabled'])
            self.owner.refresh_from_db()
            for path in paths:
                with self.subTest(policy=policy, path=path):
                    method = 'delete' if '/delete/' in path else 'post'
                    result = getattr(self.client, method)('/api/'+path,
                        {'type_mouvement':'VENTE'}, format='json')
                    self.assertEqual(result.status_code, 403, result.data)
            for path in ('oeufs/collectes/1/', 'oeufs/ventes/1/', 'oeufs/alimentation/1/',
                         'alimentation/distributions/1/'):
                for method in ('patch','put','delete'):
                    # Egg sale has no PUT/PATCH route; a 405 is expected there.
                    result = getattr(self.client, method)('/api/'+path, {}, format='json')
                    self.assertIn(result.status_code, (403,405), result.data)
        self.assertEqual(AuditEvent.objects.count(), initial)
        self.assertEqual(Lot.objects.count(), 0)

    def test_no_rights_and_distinct_jean_paul_rights(self):
        with self.assertRaises(PermissionDenied):
            authorize(self.jean, self.farm, 'purchases.create')
        self.grant(self.jean, 'purchases.create', 'mortality.create')
        self.grant(self.paul, 'animal_sales.create', 'births.create')
        authorize(self.jean, self.farm, 'purchases.create')
        authorize(self.paul, self.farm, 'animal_sales.create')
        for user, action in ((self.jean,'animal_sales.create'),(self.paul,'purchases.create')):
            with self.assertRaises(PermissionDenied):
                authorize(user,self.farm,action)
        self.client.force_authenticate(self.paul)
        self.assertEqual(self.client.post('/api/achats/create/',{},format='json').status_code,403)

    def test_foreign_species_cannot_be_used_for_purchase(self):
        self.grant(self.jean, 'purchases.create')
        foreign = Espece.objects.create(nom='Foreign', exploitation=self.other.exploitation)
        self.client.force_authenticate(self.jean)
        payload = {'nom_lot':'Synthetic lot','espece':foreign.pk,'quantite':2,
                   'prix_total':'10.00','prix_unitaire':'5.00','date':'2026-10-11'}
        result = self.client.post('/api/achats/create/',payload,format='json')
        self.assertEqual(result.status_code,400,result.data)
        self.assertFalse(Lot.objects.exists())
        payload['espece']=self.species.pk
        result = self.client.post('/api/achats/create/',payload,format='json')
        self.assertEqual(result.status_code,201,result.data)

    def test_finance_for_all_active_own_farm_and_tenant_scope(self):
        for user in (self.owner,self.jean,self.paul):
            authorize(user,self.farm,'finance.read')
            self.client.force_authenticate(user)
            self.assertEqual(self.client.get('/api/dashboard/').status_code,200)
            self.assertIn('finance.read',permission_contract(user)['permissions'])
            with self.assertRaises(PermissionDenied):
                authorize(user,self.other.exploitation,'finance.read')
        self.jean.memberships.update(is_active=False)
        self.client.force_authenticate(self.jean)
        self.assertEqual(self.client.get('/api/dashboard/').status_code,403)

    def test_owner_administration_permissions_versions_audit(self):
        self.client.force_authenticate(self.owner)
        result=self.client.post('/api/memberships/',{'username':'new-foundations',
            'password':'Synthetic-unique-827!','permissions':['purchases.create']},format='json')
        self.assertEqual(result.status_code,201,result.data)
        member=ExploitationMembership.objects.get(pk=result.data['id'])
        self.assertEqual(member.user.exploitation_id,self.farm.pk)
        self.assertNotIn('password',result.data)
        self.assertEqual(list(member.action_permissions.values_list('code',flat=True)),['purchases.create'])
        result=self.client.patch(f'/api/memberships/{member.pk}/',
            {'permissions':['mortality.create'],'expected_version':1},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['version'],2)
        self.assertEqual(result.data['permissions'],['mortality.create'])
        stale=self.client.patch(f'/api/memberships/{member.pk}/',
            {'permissions':[],'expected_version':1},format='json')
        self.assertEqual(stale.status_code,400)
        self.assertTrue(AuditEvent.objects.filter(entity_type='core.membershippermission',category='SECURITY').exists())

    def test_permission_codes_and_role_elevation_rejected(self):
        self.client.force_authenticate(self.owner)
        member=self.jean.memberships.first()
        for payload in ({'role':'OWNER'}, {'permissions':['users.manage'],'expected_version':1},
                        {'permissions':['finance.read'],'expected_version':1},
                        {'permissions':['invented.create'],'expected_version':1}):
            result=self.client.patch(f'/api/memberships/{member.pk}/',payload,format='json')
            self.assertEqual(result.status_code,400,result.data)
        self.client.force_authenticate(self.jean)
        self.assertEqual(self.client.patch(f'/api/memberships/{member.pk}/',
            {'permissions':['purchases.create'],'expected_version':1},format='json').status_code,403)

    def test_disabled_login_and_refresh_remain_denied_after_reactivation(self):
        member=self.jean.memberships.first()
        result=self.client.post('/api/token/',{'username':self.jean.username,
            'password':'Synthetic-only-password-827!'},format='json')
        self.assertEqual(result.status_code,200,result.data)
        refresh=result.data['refresh']
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.patch(f'/api/memberships/{member.pk}/',
            {'is_active':False},format='json').status_code,200)
        self.client.force_authenticate(None)
        result=self.client.post('/api/token/',{'username':self.jean.username,
            'password':'Synthetic-only-password-827!'},format='json')
        self.assertEqual(result.status_code,403,result.data)
        self.client.force_authenticate(self.owner)
        self.client.patch(f'/api/memberships/{member.pk}/',{'is_active':True},format='json')
        self.client.force_authenticate(None)
        self.assertEqual(self.client.post('/api/token/refresh/',{'refresh':refresh},format='json').status_code,401)

    def test_agenda_delegation_fields_versions_and_cancellation(self):
        self.client.force_authenticate(self.jean)
        payload={'title':'Synthetic task','date':'2026-10-11'}
        self.assertEqual(self.client.post('/api/tasks/',payload,format='json').status_code,403)
        self.grant(self.jean,'agenda.plan','agenda.read')
        result=self.client.post('/api/tasks/',payload,format='json')
        self.assertEqual(result.status_code,201,result.data)
        pk=result.data['id']
        result=self.client.patch(f'/api/tasks/{pk}/',{'status':'CANCELLED','expected_version':1},format='json')
        self.assertEqual(result.status_code,403,result.data)
        self.client.force_authenticate(self.owner)
        result=self.client.patch(f'/api/tasks/{pk}/',{'status':'CANCELLED','expected_version':1,
            'cancellation_reason':'Synthetic cancellation'},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['version'],2)
        self.assertEqual(self.client.delete(f'/api/tasks/{pk}/').status_code,403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.get('/api/tasks/').status_code,200)

    def test_agenda_progress_cannot_plan_or_assign_foreign_user(self):
        self.grant(self.jean,'agenda.read','agenda.progress')
        task=Task.objects.create(exploitation=self.farm,title='Progress',date='2026-10-11',assigned_to=self.jean)
        self.client.force_authenticate(self.jean)
        result=self.client.patch(f'/api/tasks/{task.pk}/',{'title':'Hijack','expected_version':1},format='json')
        self.assertEqual(result.status_code,403,result.data)
        result=self.client.patch(f'/api/tasks/{task.pk}/',{'status':'IN_PROGRESS','expected_version':1},format='json')
        self.assertEqual(result.status_code,200,result.data)
        self.client.force_authenticate(self.owner)
        result=self.client.patch(f'/api/tasks/{task.pk}/',{'assigned_to':self.other.pk,'expected_version':2},format='json')
        self.assertEqual(result.status_code,400,result.data)

    def test_direct_service_owner_no_context_cannot_create_business(self):
        from core.terrain_application import HANDLERS
        row=SimpleNamespace(exploitation_id=self.farm.pk)
        for kind,handler in HANDLERS.items():
            with self.subTest(kind=kind), self.assertRaises(PermissionDenied):
                handler(row,self.owner)
        with self.assertRaises(PermissionDenied):
            authorize_business_service(self.owner,self.farm,'purchases.create')

    def test_prepared_offline_requires_fine_contract_and_current_rights(self):
        member=self.grant(self.jean,'purchases.create')
        original={'client_operation_id':'synthetic-original','payload':{'untouched':True}}
        before=copy.deepcopy(original)
        for contract in ({}, {'version':0}):
            with self.assertRaises(PermissionDenied):
                authorize_prepared_offline(self.jean,self.farm,'ACHAT','CREATE',contract)
        contract=permission_contract(self.jean)
        for malformed in (dict(contract,version=True),
                          dict(contract,permissions=['purchases.create','invented.create']),
                          dict(contract,permissions=['purchases.create','purchases.create']),
                          dict(contract,role='OWNER')):
            with self.assertRaises(PermissionDenied):
                authorize_prepared_offline(self.jean,self.farm,'ACHAT','CREATE',malformed)
        authorize_prepared_offline(self.jean,self.farm,'ACHAT','CREATE',contract)
        member.version+=1;member.save(update_fields=['version'])
        with self.assertRaises(PermissionDenied):
            authorize_prepared_offline(self.jean,self.farm,'ACHAT','CREATE',contract)
        self.assertEqual(original,before)

    def test_owner_membership_coherence_diagnostic_does_not_repair(self):
        self.assertEqual(owner_inconsistencies(apps),[])
        member=self.jean.memberships.first();member.role='OWNER'
        with self.assertRaises(ModelValidationError):member.save()
        self.assertEqual(ExploitationMembership.objects.get(pk=member.pk).role,'OPERATEUR')
        self.farm.proprietaire=self.jean
        self.farm.save(update_fields=['proprietaire'])
        self.assertEqual(owner_inconsistencies(apps),[self.farm.pk])
        self.assertEqual(self.farm.proprietaire_id,self.jean.pk)

    def test_stock_readers_agree_after_compensation_without_stock_rewrite(self):
        from core.views import get_lot_stock
        from core.models import Achat, Mouvement, TerrainSubmission, DeviceRegistration
        from django.utils import timezone
        import uuid
        lot=Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Compensated',date_debut='2026-10-11')
        Achat.objects.create(lot=lot,exploitation=self.farm,quantite=10,prix_total='20.00',
                             prix_unitaire='2.00',date='2026-10-11')
        Mouvement.objects.create(lot=lot,exploitation=self.farm,type_mouvement='ACHAT',
                                 quantite=10,date='2026-10-11')
        device=DeviceRegistration.objects.create(exploitation=self.farm,
            installation_uuid=uuid.uuid4(),display_name='Synthetic',public_key='synthetic-no-key')
        member=self.jean.memberships.first()
        grant=OfflineAuthorization.objects.create(membership=member,device=device,capabilities={},
            rights_version=member.version,write_generation=1,expires_at=timezone.now())
        row=TerrainSubmission.objects.create(exploitation=self.farm,device=device,
            offline_authorization=grant,client_operation_id=uuid.uuid4(),local_sequence=1,
            author_user_id=self.jean.pk,author_membership_id=member.pk,device_generation=1,
            entity_type='ACHAT',operation_type='CREATE',payload={},business_occurred_at=timezone.now(),
            local_recorded_at=timezone.now(),declaration_digest='a'*64)
        TerrainStockAdjustment.objects.create(exploitation=self.farm,submission=row,
            decision_uuid=uuid.uuid4(),lot=lot,kind='ANIMAL',signed_quantity=-2)
        from core.utils import get_lot_stock as utility_stock
        self.assertEqual(get_lot_stock(lot),8)
        self.assertEqual(utility_stock(lot),8)
        self.assertEqual(Mouvement.objects.get(lot=lot).quantite_signee,10)
        self.assertEqual(Achat.objects.get(lot=lot).quantite,10)
        self.assertEqual(lot.stock,8)


class PermissionConstraintTests(TransactionTestCase):
    def test_unknown_code_and_inactive_owner_database_constraints(self):
        owner=User.objects.create_user(username='known-owner')
        user=User.objects.create_user(username='known-operator',exploitation=owner.exploitation)
        member=user.memberships.first()
        # bulk_create/update bypass model validation: the database must reject them.
        with self.assertRaises(IntegrityError), transaction.atomic():
            MembershipPermission.objects.bulk_create([
                MembershipPermission(membership=member,code='invented.create')])
        with self.assertRaises(IntegrityError), transaction.atomic():
            owner.memberships.update(is_active=False)

    def test_preflight_inconsistent_owner_stops_without_repair(self):
        import importlib
        migration=importlib.import_module('core.migrations.0032_membershippermission_and_more')
        owner=User.objects.create_user(username='preflight-owner')
        user=User.objects.create_user(username='preflight-operator',exploitation=owner.exploitation)
        farm=owner.exploitation
        farm.proprietaire=user;farm.save(update_fields=['proprietaire'])
        schema=SimpleNamespace(connection=connection)
        with self.assertRaisesRegex(RuntimeError,'DIAGNOSTIC_REQUIRED'):
            migration.owner_preflight(apps,schema)
        farm.refresh_from_db()
        self.assertEqual(farm.proprietaire_id,user.pk)
        self.assertEqual(owner.memberships.get().role,'OWNER')

    def test_right_revoked_while_writer_waits_for_farm_lock_has_no_effect(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Real row lock proof requires PostgreSQL')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Event
        from django.db import close_old_connections
        from core.models import Exploitation
        from core.business_authorization import guarded_handler
        owner=User.objects.create_user(username='concurrent-right-owner')
        user=User.objects.create_user(username='concurrent-right-operator',exploitation=owner.exploitation)
        member=user.memberships.first()
        MembershipPermission.objects.create(membership=member,code='clients.create')
        entered=Event()
        effects=[]
        guarded=guarded_handler('clients.create',lambda row,actor: effects.append(actor.pk))
        def writer():
            close_old_connections()
            try:
                entered.set()
                try:
                    guarded(SimpleNamespace(exploitation_id=owner.exploitation_id),user)
                except PermissionDenied:
                    return 'denied'
                return 'applied'
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=1) as pool:
            with transaction.atomic():
                Exploitation.objects.select_for_update().get(pk=owner.exploitation_id)
                pending=pool.submit(writer)
                self.assertTrue(entered.wait(5))
                member.action_permissions.all().delete()
                member.version+=1;member.save(update_fields=['version'])
            self.assertEqual(pending.result(timeout=10),'denied')
        self.assertEqual(effects,[])

    def test_unique_permission_database_constraint(self):
        owner=User.objects.create_user(username='constraint-owner')
        user=User.objects.create_user(username='constraint-operator',exploitation=owner.exploitation)
        member=user.memberships.first()
        MembershipPermission.objects.create(membership=member,code='purchases.create')
        with self.assertRaises(IntegrityError), transaction.atomic():
            MembershipPermission.objects.create(membership=member,code='purchases.create')

    def test_postgresql_owner_partial_unique_constraint(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL constraint proof requires PostgreSQL')
        owner=User.objects.create_user(username='unique-owner')
        user=User.objects.create_user(username='other-owner',exploitation=owner.exploitation)
        with self.assertRaises(IntegrityError), transaction.atomic():
            user.memberships.update(role='OWNER')
