from unittest.mock import patch
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import Client, Task, TerrainSubmission, TerrainEntityMapping, AuditEvent
from core.tests.test_terrain_transport import TransportFixture
from core.tests import test_terrain_transport as transport_tests


class TerrainApplicationTests(TransportFixture, TestCase):
    def client_operation(self, sequence=1):
        operation = self.operation(sequence)
        operation['entity_type'] = 'CLIENT'
        return operation

    def task_operation(self, task, sequence=1, status='IN_PROGRESS'):
        operation = self.operation(sequence)
        operation.update(entity_type='TASK', operation_type='UPDATE',
            expected_server_version=str(task.version),
            payload={'task_id':task.pk, 'status':status, 'report':'Compte rendu terrain'})
        return operation

    def test_client_is_applied_once_with_original_jean_audit_and_mapping(self):
        operation = self.client_operation()
        response, _ = self.send(operation)
        self.assertEqual(response.status_code, 200, response.data)
        receipt = response.data['receipts'][0]
        self.assertEqual(receipt['business_status'], 'CONFIRMED')
        self.assertTrue(receipt['applied_at'])
        client = Client.objects.get()
        self.assertEqual(client.created_by, self.jean)
        self.assertEqual(client.exploitation, self.farm)
        self.assertEqual(receipt['entity_mappings'][0]['server_entity_id'], client.pk)
        retry, _ = self.send(operation)
        self.assertEqual(retry.data, response.data)
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(TerrainEntityMapping.objects.count(), 1)
        audit = AuditEvent.objects.get(action='TERRAIN_APPLIED')
        self.assertEqual(audit.actor_user_id, self.jean.pk)
        self.assertEqual(audit.source, 'OFFLINE')
        self.assertEqual(str(audit.operation_id), operation['client_operation_id'])
        event = AuditEvent.objects.get(entity_type='core.client', action='CREATE')
        self.assertEqual(event.source, 'OFFLINE')
        self.assertEqual(event.operation_id, audit.operation_id)

    def test_invalid_business_payload_is_received_unchanged_for_reconciliation(self):
        operation = self.client_operation()
        operation['payload'] = {'nom':'', 'exploitation':99999}
        response, _ = self.send(operation)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['receipts'][0]['business_status'], 'NEEDS_RECONCILIATION')
        self.assertEqual(TerrainSubmission.objects.get().payload, operation['payload'])
        self.assertFalse(Client.objects.exists())
        self.assertIsNone(TerrainSubmission.objects.get().outcome.applied_at)

    def test_unexpected_application_failure_retains_received_original_then_retry_applies(self):
        operation = self.client_operation()
        client = APIClient(raise_request_exception=False)
        with patch.dict('core.terrain_application.HANDLERS', CLIENT=lambda *_: (_ for _ in ()).throw(RuntimeError('Synthetic crash'))):
            response, _ = self.send(operation, client=client)
        self.assertEqual(response.status_code, 500)
        self.assertEqual(TerrainSubmission.objects.count(), 1)
        self.assertEqual(TerrainSubmission.objects.get().outcome.business_status, 'UNREVIEWED')
        self.assertFalse(Client.objects.exists())
        retry, _ = self.send(operation)
        self.assertEqual(retry.data['receipts'][0]['business_status'], 'CONFIRMED')
        self.assertEqual(Client.objects.count(), 1)

    def test_duplicate_local_client_identity_cannot_create_second_client(self):
        first = self.client_operation()
        self.send(first)
        second = self.client_operation(2)
        second['local_entity_id'] = first['local_entity_id']
        response, _ = self.send(second)
        self.assertEqual(response.data['receipts'][0]['reason_code'], 'LOCAL_ENTITY_ALREADY_MAPPED')
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(TerrainSubmission.objects.count(), 2)

    def test_task_reporting_respects_original_author_transition_and_version(self):
        task = Task.objects.create(exploitation=self.farm, title='Visite', date=timezone.localdate(), assigned_to=self.jean)
        start = self.task_operation(task)
        response, _ = self.send(start)
        self.assertEqual(response.data['receipts'][0]['business_status'], 'CONFIRMED')
        task.refresh_from_db()
        done = self.task_operation(task, 2, 'DONE')
        response, _ = self.send(done)
        self.assertEqual(response.data['receipts'][0]['business_status'], 'CONFIRMED')
        task.refresh_from_db()
        self.assertEqual(task.status, 'DONE')
        self.assertEqual(task.version, 3)
        self.assertEqual(task.completed_by, self.jean)
        self.assertEqual(task.report, 'Compte rendu terrain')

    def test_task_owner_change_or_foreign_assignment_is_reconciled_not_overwritten(self):
        task = Task.objects.create(exploitation=self.farm, title='Visite', date=timezone.localdate(), assigned_to=self.jean)
        operation = self.task_operation(task)
        task.version = 2
        task.save()
        response, _ = self.send(operation)
        self.assertEqual(response.data['receipts'][0]['reason_code'], 'TASK_VERSION_CONFLICT')
        task.refresh_from_db()
        self.assertEqual(task.status, 'TODO')
        task.assigned_to = self.paul
        task.save()
        response, _ = self.send(self.task_operation(task, 2))
        self.assertEqual(response.data['receipts'][0]['reason_code'], 'TASK_OTHER_ASSIGNEE')
        self.assertTrue(AuditEvent.objects.filter(reason_code='TASK_OTHER_ASSIGNEE').exists())

    def test_operator_cannot_plan_task_or_modify_owner_fields(self):
        operation = self.operation()
        operation.update(entity_type='TASK', payload={'title':'Planification interdite'})
        response, _ = self.send(operation)
        self.assertEqual(response.data['receipts'][0]['reason_code'], 'TASK_OWNER_PLANNING_ONLY')
        self.assertTrue(AuditEvent.objects.filter(reason_code='TASK_OWNER_PLANNING_ONLY').exists())
        self.assertFalse(Task.objects.exists())

    def test_reversed_arrival_dependency_waits_then_applies_without_double_effect(self):
        parent = self.client_operation(1)
        child = self.client_operation(2)
        child['dependencies'] = [parent['client_operation_id']]
        response, _ = self.send(child)
        self.assertEqual(response.data['receipts'][0]['business_status'], 'WAITING_DEPENDENCY')
        self.assertFalse(Client.objects.exists())
        self.send(parent)
        response, _ = self.send(child, purpose='STATUS')
        self.assertEqual(response.data['receipts'][0]['business_status'], 'CONFIRMED')
        self.assertEqual(Client.objects.count(), 2)
        self.send(parent)
        self.assertEqual(Client.objects.count(), 2)

    def test_foreign_farm_task_is_retained_but_cannot_be_changed(self):
        from core.models import User
        other = User.objects.create_user(username='other-task-farm')
        task = Task.objects.create(exploitation=other.exploitation, title='Autre ferme', date=timezone.localdate())
        response, _ = self.send(self.task_operation(task))
        self.assertEqual(response.data['receipts'][0]['reason_code'], 'TASK_NOT_AVAILABLE')
        task.refresh_from_db()
        self.assertEqual(task.status, 'TODO')
        self.assertEqual(TerrainSubmission.objects.count(), 1)


class TerrainConcurrentApplicationTests(transport_tests.TerrainConcurrentReceiptTests):
    def operation(self, sequence=1):
        result = super().operation(sequence)
        result['entity_type'] = 'CLIENT'
        return result

    def test_two_simultaneous_receipts_of_same_uuid_have_one_effect(self):
        super().test_two_simultaneous_receipts_of_same_uuid_have_one_effect()
        self.assertEqual(Client.objects.count(), 1)
        self.assertEqual(TerrainEntityMapping.objects.count(), 1)
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_APPLIED').count(), 1)
