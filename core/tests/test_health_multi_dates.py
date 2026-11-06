from rest_framework.test import APIClient
from datetime import timedelta
from uuid import uuid4
from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from core.tests.test_terrain_operations import OperationFixture
from core.models import CategorieDepense,MembershipPermission
from core.supply_models import SupplyCategoryBinding,SupplyProduct
from core.offline_permissions import contract_for
from core.access_policy import effective_permissions
from core.models import Task,Depense,User
from core.health_models import HealthCommand,HealthTask
from core.supply_models import SupplyMovement

class HealthMultiDateTests(OperationFixture,TestCase):
    def setUp(self):
        super().setUp()
        self.category=CategorieDepense.objects.create(exploitation=self.farm,nom='Vaccin sanitaire')
        SupplyCategoryBinding.objects.create(category=self.category,purpose='VACCINE')
        self.product=SupplyProduct.objects.create(exploitation=self.farm,kind='VACCINE',name='Vaccin explicitement choisi')
        for code in ['agenda.read','agenda.progress','vaccinations.create','vaccinations.update','vaccinations.delete','vaccinations.loss']:
            MembershipPermission.objects.get_or_create(membership=self.member,code=code)
        self.grant.capabilities.update(vaccine_stock_contract=1,health_agenda_contract=1,
            permission_contract=contract_for(self.grant,effective_permissions(self.member)));self.grant.save()
        self.api=APIClient();self.api.force_authenticate(self.owner);self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
    def body(self,**extra):
        return {'operation_uuid':str(uuid4()),'product':str(self.product.pk),'lot':self.lot.pk,
            'species':self.species.pk,'assigned_to':self.jean.pk,
            'dates':[str(timezone.localdate()+timedelta(days=d)) for d in [3,1,8]],**extra}
    def post(self,body):return self.api.post('/api/tasks/health-plan/',body,format='json')
    def test_arbitrary_dates_atomic_no_time_no_stock_and_replay(self):
        body=self.body()
        first=self.post(body);self.assertEqual(first.status_code,201,first.data)
        self.assertEqual(len(first.data),3);self.assertEqual(Task.objects.count(),3)
        self.assertTrue(all(row['health']['planned_time'] is None for row in first.data))
        self.assertFalse(SupplyMovement.objects.exists());self.assertFalse(Depense.objects.exists())
        replay=self.post(body);self.assertEqual([r['id'] for r in replay.data],[r['id'] for r in first.data])
        self.assertEqual(HealthCommand.objects.count(),1)
        changed={**body,'dates':body['dates'][:1]};self.assertEqual(self.post(changed).status_code,400)
        self.assertEqual(Task.objects.count(),3)
        new_uuid={**body,'operation_uuid':str(uuid4())};self.assertEqual(self.post(new_uuid).status_code,201)
        self.assertEqual(Task.objects.count(),3)
    def test_single_date_array_and_invalid_combinations(self):
        body=self.body();self.assertEqual(self.post({**body,'dates':body['dates'][:1]}).status_code,201)
        for extra in [{'dates':[]},{'dates':[body['dates'][0],body['dates'][0]]},{'date':body['dates'][0]},
            {'interval_days':2,'end_date':body['dates'][-1]},{'dates':['2026-02-30']},{'dates':[True]},
            {'dates':[str(timezone.localdate()),str(timezone.localdate()+timedelta(days=366))]}]:
            with self.subTest(extra=extra):self.assertEqual(self.post({**self.body(),**extra}).status_code,400)
        self.assertEqual(Task.objects.count(),1)
    def test_second_occurrence_failure_rolls_back_all_tasks(self):
        from core.task_views import AgendaSerializer
        original=AgendaSerializer.save;calls=0
        def fail(serializer,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:
                from rest_framework.exceptions import ValidationError
                raise ValidationError('SYNTHETIC_SECOND_DATE_REJECTED')
            return original(serializer,**kwargs)
        body=self.body()
        with patch.object(AgendaSerializer,'save',fail):self.assertEqual(self.post(body).status_code,400)
        self.assertFalse(Task.objects.exists());self.assertFalse(HealthTask.objects.exists());self.assertFalse(HealthCommand.objects.exists())
        self.assertEqual(self.post(body).status_code,201)
    def test_foreign_farm_and_unauthorized_operator_refused(self):
        body=self.body();other=User.objects.create_user(username='foreign-multidate-owner')
        self.api.force_authenticate(other);self.assertEqual(self.post(body).status_code,400)
        self.api.force_authenticate(self.jean);self.assertEqual(self.post(body).status_code,403)
        self.assertFalse(Task.objects.exists());self.assertFalse(SupplyMovement.objects.exists())
