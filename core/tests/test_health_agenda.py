from uuid import uuid4
from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.tests.test_terrain_operations import OperationFixture
from core.tests import test_vaccines as vaccine_tests
from core.models import Task,MembershipPermission,CategorieDepense,TerrainSubmission,User,Lot,Espece
from core.supply_models import SupplyCategoryBinding,SupplyProduct,SupplyMovement
from core.health_models import HealthTask,HealthCommand,VaccinationTaskLink
from core.offline_permissions import contract_for
from core.access_policy import effective_permissions


class HealthAgendaTests(OperationFixture,TestCase):
    body=vaccine_tests.VaccineTests.body
    buy=vaccine_tests.VaccineTests.buy
    vials=vaccine_tests.VaccineTests.vials
    vaccination=vaccine_tests.VaccineTests.vaccination
    stock=vaccine_tests.VaccineTests.stock
    physical=vaccine_tests.VaccineTests.physical

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

    def plan(self,**extra):
        body={'operation_uuid':str(uuid4()),'product':str(self.product.pk),'lot':self.lot.pk,'species':self.species.pk,
            'date':str(timezone.localdate()+timedelta(days=2)),'assigned_to':self.jean.pk,**extra}
        response=self.api.post('/api/tasks/health-plan/',body,format='json')
        self.assertEqual(response.status_code,201,response.data)
        return body,response.data

    def test_explicit_single_and_recurrence_reuse_tasks_and_are_idempotent(self):
        body,rows=self.plan();self.assertEqual(len(rows),1);self.assertIsNone(rows[0]['health']['planned_time'])
        self.assertIsNone(rows[0]['health']['interval_days']);self.assertEqual(Task.objects.count(),1)
        again=self.api.post('/api/tasks/health-plan/',body,format='json');self.assertEqual(again.status_code,201);self.assertEqual(Task.objects.count(),1)
        day=timezone.localdate()+timedelta(days=10)
        body,rows=self.plan(date=str(day),interval_days=7,end_date=str(day+timedelta(days=21)),planned_time='09:30')
        self.assertEqual(len(rows),4);self.assertEqual(Task.objects.count(),5)
        self.api.post('/api/tasks/health-plan/',body,format='json');self.assertEqual(Task.objects.count(),5)
        self.api.post('/api/tasks/health-plan/',{**body,'operation_uuid':str(uuid4())},format='json');self.assertEqual(Task.objects.count(),5)
        response=self.api.post('/api/tasks/health-plan/',{**body,'interval_days':3},format='json');self.assertEqual(response.status_code,400)

    def test_task_done_never_implies_real_vaccination_or_stock_consumption(self):
        self.buy();_,rows=self.plan(date=str(timezone.localdate()-timedelta(days=1)))
        task=Task.objects.get(pk=rows[0]['id'])
        for seq,status in [(1,'IN_PROGRESS'),(2,'DONE')]:
            task.refresh_from_db();original=self.terrain('TASK',{'task_id':task.pk,'status':status,'report':'Compte rendu logistique'},seq)
            original.update(operation_type='UPDATE',expected_server_version=str(task.version))
            self.assertEqual(self.apply(original)['business_status'],'CONFIRMED')
        task.refresh_from_db();self.assertEqual(task.status,'DONE');self.assertEqual(self.physical(),200)
        data=self.api.get(f'/api/tasks/{task.pk}/').data
        self.assertFalse(data['health']['fulfilled']);self.assertTrue(data['health']['overdue']);self.assertIsNone(data['health']['actual'])
        self.assertEqual(Task.objects.count(),1)

    def test_actual_early_late_links_once_with_no_second_stock_deduction(self):
        self.buy()
        for index,offset in enumerate([1,-1]):
            _,rows=self.plan(date=str(timezone.localdate()+timedelta(days=offset)))
            body=self.vaccination(animals=1,allocations=[{'vial':str(self.vials()[index].pk),'doses':1}],
                task_id=rows[0]['id'],task_version=rows[0]['version'])
            first=self.api.post('/api/vaccinations/',body,format='json');self.assertEqual(first.status_code,201,first.data)
            self.api.post('/api/vaccinations/',body,format='json')
            task=self.api.get(f"/api/tasks/{rows[0]['id']}/").data
            self.assertTrue(task['health']['fulfilled']);self.assertEqual(task['health']['actual']['difference_days'],-offset)
            self.assertEqual(task['health']['actual']['operator'],self.owner.pk);self.assertEqual(task['status'],'TODO')
            duplicate=self.api.post('/api/vaccinations/',{**body,'operation_uuid':str(uuid4()),'task_version':task['version']},format='json')
            self.assertEqual(duplicate.status_code,400)
        self.assertEqual(self.physical(),198);self.assertEqual(VaccinationTaskLink.objects.count(),2)

    def test_reschedule_cancel_and_audit_history_preserve_original_plan(self):
        _,rows=self.plan(planned_time='10:00');task=rows[0]
        body={'operation_uuid':str(uuid4()),'expected_version':task['version'],'date':str(timezone.localdate()+timedelta(days=7)),
            'planned_time':'11:15','reason':'Report explicitement demandé'}
        url=f"/api/tasks/{task['id']}/health-move/"
        first=self.api.post(url,body,format='json');self.assertEqual(first.status_code,200,first.data)
        again=self.api.post(url,body,format='json');self.assertEqual(again.data['version'],first.data['version'])
        self.assertEqual(first.data['health']['original_date'],task['date']);self.assertNotEqual(first.data['date'],task['date'])
        self.assertEqual(self.api.post(url,{**body,'operation_uuid':str(uuid4())},format='json').status_code,400)
        generic=self.api.patch(f"/api/tasks/{task['id']}/",{'date':task['date'],'expected_version':first.data['version']},format='json')
        self.assertEqual(generic.status_code,400)
        cancelled=self.api.patch(f"/api/tasks/{task['id']}/",{'status':'CANCELLED','cancellation_reason':'Rendez-vous annulé','expected_version':first.data['version']},format='json')
        self.assertEqual(cancelled.status_code,200,cancelled.data)
        history=self.api.get(f"/api/tasks/{task['id']}/health-history/").data
        self.assertTrue(any(entry['before'].get('date')==task['date'] and entry['after'].get('date')==body['date'] for entry in history))
        self.assertTrue(any(entry['after'].get('status')=='CANCELLED' for entry in history))

    def test_assignee_requires_real_permissions_and_operator_cannot_plan_without_right(self):
        options=self.api.get('/api/tasks/health-assignees/');self.assertEqual(options.status_code,200);self.assertIn(self.jean.pk,[row['user'] for row in options.data])
        MembershipPermission.objects.filter(membership=self.member,code='vaccinations.create').delete()
        body={'operation_uuid':str(uuid4()),'product':str(self.product.pk),'species':self.species.pk,'lot':self.lot.pk,'date':str(timezone.localdate()),'assigned_to':self.jean.pk}
        self.assertEqual(self.api.post('/api/tasks/health-plan/',body,format='json').status_code,400)
        self.api.force_authenticate(self.jean)
        self.assertEqual(self.api.post('/api/tasks/health-plan/',body,format='json').status_code,403)

    def test_offline_link_is_confirmed_only_by_receipt_and_late_task_change_conflicts(self):
        self.buy();_,rows=self.plan();task=rows[0]
        body=self.vaccination(animals=1,allocations=[{'vial':str(self.vials()[0].pk),'doses':1}],task_id=task['id'],task_version=task['version'])
        body.pop('operation_uuid');body.pop('vaccinated_at');body['vaccine_version']=1;body['lot_ref']={'server_id':body.pop('lot')}
        original=self.terrain('VACCINATION',body,1)
        result=self.apply(original);self.assertEqual(result['business_status'],'CONFIRMED',result)
        self.assertTrue(result['health_task_snapshot']['health']['fulfilled']);self.assertEqual(self.physical(),199)
        self.apply(original);self.assertEqual(self.physical(),199)
        _,rows=self.plan(date=str(timezone.localdate()+timedelta(days=5)))
        task=rows[0];body.update(task_id=task['id'],task_version=task['version'])
        original=self.terrain('VACCINATION',body,2)
        self.api.post(f"/api/tasks/{task['id']}/health-move/",{'operation_uuid':str(uuid4()),'expected_version':task['version'],
            'date':str(timezone.localdate()+timedelta(days=6)),'reason':'Autre appareil'},format='json')
        self.assertEqual(self.apply(original)['business_status'],'NEEDS_RECONCILIATION');self.assertEqual(self.physical(),199)
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=original['client_operation_id']).payload,original['payload'])

    def test_revocation_before_signed_link_prevents_consumption(self):
        self.buy();_,rows=self.plan();task=rows[0]
        body=self.vaccination(animals=1,allocations=[{'vial':str(self.vials()[0].pk),'doses':1}],task_id=task['id'],task_version=task['version'])
        body.pop('operation_uuid');body.pop('vaccinated_at');body['lot_ref']={'server_id':body.pop('lot')};body['vaccine_version']=1
        MembershipPermission.objects.filter(membership=self.member,code='agenda.progress').delete()
        original=self.terrain('VACCINATION',body,1);self.assertEqual(self.apply(original)['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(self.physical(),200);self.assertEqual(VaccinationTaskLink.objects.count(),0)

    def test_foreign_task_cannot_be_read_or_linked(self):
        self.buy();other=User.objects.create_user(username='other-health-farm',email='other-health@example.invalid',password='synthetic-password')
        task=Task.objects.create(exploitation=other.exploitation,title='Autre exploitation',date=timezone.localdate())
        self.assertEqual(self.api.get(f'/api/tasks/{task.pk}/').status_code,404)
        body=self.vaccination(animals=1,allocations=[{'vial':str(self.vials()[0].pk),'doses':1}],task_id=task.pk,task_version=task.version)
        self.assertEqual(self.api.post('/api/vaccinations/',body,format='json').status_code,400);self.assertEqual(self.physical(),200)
    def test_cancelled_real_vaccination_restores_stock_but_keeps_task_and_clinical_history(self):
        self.buy();_,rows=self.plan();task=rows[0]
        body=self.vaccination(animals=1,allocations=[{'vial':str(self.vials()[0].pk),'doses':1}],task_id=task['id'],task_version=task['version'])
        response=self.api.post('/api/vaccinations/',body,format='json');self.assertEqual(response.status_code,201,response.data)
        cancel={'operation_uuid':str(uuid4()),'expected_version':response.data['version'],'reason':'Administration saisie par erreur','confirm':True}
        self.assertEqual(self.api.delete(f"/api/vaccinations/{response.data['id']}/",cancel,format='json').status_code,200)
        current=self.api.get(f"/api/tasks/{task['id']}/").data
        self.assertFalse(current['health']['fulfilled']);self.assertIsNone(current['health']['actual'])
        self.assertEqual(current['status'],'TODO');self.assertEqual(self.physical(),200)
        self.assertEqual(current['health']['administration_history'],[{'vaccination':response.data['id'],'voided':True}])

    def test_missing_recurrence_end_or_boolean_interval_never_invents_a_frequency(self):
        body={'operation_uuid':str(uuid4()),'product':str(self.product.pk),'lot':self.lot.pk,'species':self.species.pk,
            'date':str(timezone.localdate()),'assigned_to':self.jean.pk,'interval_days':7}
        self.assertEqual(self.api.post('/api/tasks/health-plan/',body,format='json').status_code,400)
        self.assertEqual(self.api.post('/api/tasks/health-plan/',{**body,'interval_days':True,'end_date':str(timezone.localdate()+timedelta(days=2))},format='json').status_code,400)
        self.assertEqual(Task.objects.count(),0);self.assertEqual(HealthCommand.objects.count(),0)

    def test_health_facts_cannot_be_changed_or_deleted_by_model_updates(self):
        from django.core.exceptions import ValidationError
        _,rows=self.plan();command=HealthCommand.objects.get()
        with self.assertRaises(ValidationError):command.delete()
        with self.assertRaises(ValidationError):HealthCommand.objects.filter(pk=command.pk).update(kind='MOVE')
        command.kind='MOVE'
        with self.assertRaises(ValidationError):command.save()

    def test_historical_task_receipt_keeps_its_old_shape_and_health_metadata_is_additive(self):
        _,rows=self.plan();task=rows[0]
        original=self.terrain('TASK',{'task_id':task['id'],'status':'IN_PROGRESS','report':'État logistique'},1)
        original.update(operation_type='UPDATE',expected_server_version=str(task['version']))
        result=self.apply(original);self.assertEqual(result['business_status'],'CONFIRMED')
        self.assertNotIn('health',result['task_snapshot']);self.assertIn('health',result['health_task_snapshot'])
        self.assertFalse(result['health_task_snapshot']['health']['fulfilled'])
    def test_owner_can_classify_an_unapplied_health_report_without_reapproval_or_deleting_original(self):
        _,rows=self.plan();task=rows[0]
        moved=self.api.post(f"/api/tasks/{task['id']}/health-move/",{'operation_uuid':str(uuid4()),'expected_version':task['version'],
            'date':str(timezone.localdate()+timedelta(days=3)),'reason':'Autre appareil'},format='json')
        original=self.terrain('TASK',{'task_id':task['id'],'status':'IN_PROGRESS','report':'Compte rendu initial conservé'},1)
        original.update(operation_type='UPDATE',expected_server_version=str(task['version']))
        self.assertEqual(self.apply(original)['business_status'],'NEEDS_RECONCILIATION')
        url=f"/api/offline/reconciliation/{original['client_operation_id']}/"
        self.assertEqual(self.api.get(url).data['reconciliation_mode'],'FEED_CANCEL_ONLY')
        decision={'decision_uuid':str(uuid4()),'expected_decision_version':0,'action':'CANCEL','reason':'Ancien rendez-vous reporté, rapport non appliqué'}
        self.assertEqual(self.api.post(url,{**decision,'action':'APPLY_ORIGINAL'},format='json').status_code,403)
        result=self.api.post(url,decision,format='json');self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['receipt']['business_status'],'NOT_APPLIED')
        self.assertEqual(self.api.post(url,decision,format='json').status_code,200)
        actual=Task.objects.get(pk=task['id']);self.assertEqual(actual.version,moved.data['version']);self.assertEqual(actual.status,'TODO')
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=original['client_operation_id']).payload,original['payload'])
    def test_correction_into_loss_does_not_count_as_vaccination_and_cannot_restore_a_duplicate(self):
        self.buy();_,rows=self.plan();task=rows[0]
        create=self.vaccination(animals=1,allocations=[{'vial':str(self.vials()[0].pk),'doses':1}],task_id=task['id'],task_version=task['version'])
        first=self.api.post('/api/vaccinations/',create,format='json');self.assertEqual(first.status_code,201,first.data)
        correction={'operation_uuid':str(uuid4()),'expected_version':first.data['version'],'reason':'Il s’agissait d’une perte',
            'vaccinated_at':first.data['vaccinated_at'],'animals':0,'allocations':[{'vial':str(self.vials()[0].pk),'doses':0,'lost':1}]}
        edited=self.api.patch(f"/api/vaccinations/{first.data['id']}/",correction,format='json');self.assertEqual(edited.status_code,200,edited.data)
        task=self.api.get(f"/api/tasks/{task['id']}/").data
        self.assertFalse(task['health']['fulfilled']);self.assertIsNone(task['health']['actual']);self.assertEqual(self.physical(),199)
        second=self.api.post('/api/vaccinations/',self.vaccination(animals=1,allocations=[{'vial':str(self.vials()[1].pk),'doses':1}],
            task_id=task['id'],task_version=task['version']),format='json');self.assertEqual(second.status_code,201,second.data)
        duplicate={**correction,'operation_uuid':str(uuid4()),'expected_version':edited.data['version'],'animals':1,
            'allocations':[{'vial':str(self.vials()[0].pk),'doses':1}]}
        self.assertEqual(self.api.patch(f"/api/vaccinations/{first.data['id']}/",duplicate,format='json').status_code,400)
        self.assertEqual(self.physical(),198);self.assertEqual(VaccinationTaskLink.objects.count(),2)

    def test_revoked_read_cannot_disclose_sanitary_metadata_through_task_progress_receipt(self):
        _,rows=self.plan();task=rows[0]
        MembershipPermission.objects.filter(membership=self.member,code='agenda.read').delete()
        original=self.terrain('TASK',{'task_id':task['id'],'status':'IN_PROGRESS','report':'Rapport uniquement'},1)
        original.update(operation_type='UPDATE',expected_server_version=str(task['version']))
        result=self.apply(original);self.assertEqual(result['business_status'],'CONFIRMED',result)
        self.assertNotIn('health_task_snapshot',result);self.assertNotIn('health',result['task_snapshot'])
        self.api.force_authenticate(self.jean)
        self.assertEqual(self.api.get(f"/api/tasks/{task['id']}/").status_code,403)
