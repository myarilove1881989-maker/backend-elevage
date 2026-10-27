from uuid import uuid4
from unittest import skipUnless
from decimal import Decimal
from django.test import TestCase, TransactionTestCase
from django.db import connection, close_old_connections
from django.db import transaction, DatabaseError
from rest_framework.test import APIClient
from core.models import (User, MembershipPermission, Espece, Lot, Depense, Task,
                         ExpenseCancellation, AuditEvent, ConsommationAliment)
from core.audit import audit_scope, ActorContext


class TraceableCancellationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='cancel-owner')
        self.operator = User.objects.create_user(username='cancel-operator', exploitation=self.owner.exploitation)
        self.foreign = User.objects.create_user(username='cancel-foreign')
        self.farm = self.owner.exploitation
        self.species = Espece.objects.create(nom='Synthetic', exploitation=self.farm)
        self.lot = Lot.objects.create(nom='Synthetic', exploitation=self.farm, espece=self.species, date_debut='2026-10-12')
        self.expense = Depense.objects.create(lot=self.lot, montant='25.00', created_by=self.operator)
        self.client = APIClient()
        self.payload = {'reason': 'Synthetic expense correction', 'operation_uuid': str(uuid4())}

    def grant(self, code):
        MembershipPermission.objects.create(membership=self.operator.memberships.get(), code=code)

    def cancel(self, user=None, payload=None, pk=None):
        self.client.force_authenticate(user or self.operator)
        return self.client.post(f'/api/depenses/cancel/{pk or self.expense.pk}/', payload or self.payload, format='json')

    def test_exact_permission_owner_denied_reason_and_tenant(self):
        self.grant('expenses.create')
        self.assertEqual(self.cancel().status_code, 403)
        self.assertEqual(self.cancel(self.owner).status_code, 403)
        self.grant('expenses.delete')
        self.assertEqual(self.cancel(payload={'operation_uuid': str(uuid4())}).status_code, 400)
        self.assertEqual(self.cancel(self.foreign).status_code, 403)
        self.assertEqual(self.cancel(pk=99999).status_code, 404)
        self.assertTrue(Depense.objects.filter(pk=self.expense.pk).exists())

    def test_original_money_stock_audit_and_unique_replay(self):
        self.grant('expenses.delete')
        self.client.force_authenticate(self.operator)
        before_finance = self.client.get('/api/dashboard/').data['kpis']
        stock = self.lot.stock
        result = self.cancel()
        self.assertEqual(result.status_code, 200, result.data)
        original = Depense.history.get(pk=self.expense.pk)
        self.assertEqual(original.montant, Decimal('25.00'))
        self.assertEqual(original.created_by_id, self.operator.pk)
        self.assertIsNotNone(original.voided_at)
        self.assertEqual(self.lot.stock, stock)
        self.assertFalse(Depense.objects.filter(pk=original.pk).exists())
        after_finance = self.client.get('/api/dashboard/').data['kpis']
        self.assertEqual(Decimal(str(before_finance['depenses'])) - Decimal(str(after_finance['depenses'])), Decimal('25.00'))
        for key in ('stock', 'chiffre_affaires', 'performance'):
            self.assertEqual(before_finance[key], after_finance[key])
        events = AuditEvent.objects.count()
        replay = self.cancel()
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.data['replayed'])
        self.assertEqual(AuditEvent.objects.count(), events)
        self.assertEqual(ExpenseCancellation.objects.count(), 1)
        record = ExpenseCancellation.objects.get()
        self.assertEqual(record.reason, self.payload['reason'])
        self.assertEqual(record.author_id, self.operator.pk)
        self.assertTrue(AuditEvent.objects.filter(reason_text=record.reason, entity_type='core.depense').exists())
        self.assertEqual(self.cancel(payload={**self.payload, 'operation_uuid': str(uuid4())}).status_code, 400)
        self.assertEqual(self.cancel(payload={**self.payload, 'reason': 'Different'}).status_code, 400)

    def test_v1_original_cannot_use_new_rest_cancellation(self):
        self.grant('expenses.delete')
        with audit_scope(ActorContext(self.operator.pk, self.farm.pk, source='OFFLINE', operation_id=uuid4())):
            self.expense.note = 'Original V1'
            self.expense.save()
        count = AuditEvent.objects.count()
        self.assertEqual(self.cancel().status_code, 400)
        self.assertEqual(AuditEvent.objects.count(), count)
        self.assertFalse(ExpenseCancellation.objects.exists())

    def test_source_and_task_physical_delete_rejected_in_online_scope(self):
        from django.contrib import admin
        from types import SimpleNamespace
        self.owner.is_superuser = True
        request = SimpleNamespace(user=self.owner)
        for model in (Depense, Task, User):
            self.assertFalse(admin.site._registry[model].has_delete_permission(request))
        self.assertFalse(admin.site._registry[Task].has_change_permission(request))
        with self.assertRaises(Exception) as error:
            with audit_scope(ActorContext(self.operator.pk, self.farm.pk)):
                self.expense.delete()
        self.assertIn('HISTORY_IMMUTABLE', str(error.exception))
        self.assertTrue(Depense.objects.filter(pk=self.expense.pk).exists())
        self.client.force_authenticate(self.owner)
        self.assertIn(self.client.delete('/api/audit-events/').status_code, (403, 405))

    def test_linked_feed_cannot_be_cancelled_alone(self):
        self.grant('expenses.delete')
        ConsommationAliment.objects.create(exploitation=self.farm, lot=self.lot,
            depense=self.expense, quantite_kg='2.000')
        self.assertEqual(self.cancel().status_code, 400)
        self.assertTrue(Depense.objects.filter(pk=self.expense.pk).exists())

    @skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL cancellation trigger required; SQLite cannot validate it')
    def test_cancellation_ledger_database_immutable(self):
        self.grant('expenses.delete')
        self.assertEqual(self.cancel().status_code, 200)
        self.assertEqual(connection.vendor, 'postgresql')
        for mutation in ('UPDATE core_expensecancellation SET reason = %s', 'DELETE FROM core_expensecancellation WHERE reason = %s'):
            with self.assertRaises(DatabaseError), transaction.atomic():
                with connection.cursor() as cursor:
                    cursor.execute(mutation, ['Synthetic expense correction'])
        self.assertEqual(ExpenseCancellation.objects.count(), 1)

    def test_history_read_for_operator_tenant_and_security_exclusion(self):
        with audit_scope(ActorContext(self.operator.pk, self.farm.pk)):
            self.expense.note = 'Synthetic fact'
            self.expense.save()
        self.client.force_authenticate(self.operator)
        result = self.client.get('/api/history/')
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.data['results'])
        self.assertTrue(all(e['exploitation_id'] == self.farm.pk and e['category'] in ('BUSINESS', 'AGENDA') for e in result.data['results']))
        self.client.force_authenticate(self.foreign)
        self.assertEqual(self.client.get('/api/history/').data['count'], 0)
        # Mutation permissions fail closed before DRF's method dispatch.
        self.assertEqual(self.client.delete('/api/history/').status_code, 403)

    def test_owner_task_cancellation_reason_version_and_active_list(self):
        self.grant('agenda.plan')
        self.grant('agenda.read')
        task = Task.objects.create(exploitation=self.farm, title='Synthetic task', date='2026-10-12', created_by=self.operator)
        data = {'status': 'CANCELLED', 'expected_version': 1, 'cancellation_reason': 'Synthetic reason'}
        self.client.force_authenticate(self.operator)
        self.assertEqual(self.client.patch(f'/api/tasks/{task.pk}/', data, format='json').status_code, 403)
        self.client.force_authenticate(self.owner)
        self.assertEqual(self.client.patch(f'/api/tasks/{task.pk}/', {'status': 'CANCELLED', 'expected_version': 1}, format='json').status_code, 400)
        result = self.client.patch(f'/api/tasks/{task.pk}/', data, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        task.refresh_from_db()
        self.assertEqual(task.cancelled_by_id, self.owner.pk)
        self.assertIsNotNone(task.cancelled_at)
        self.assertEqual(task.created_by_id, self.operator.pk)
        self.assertEqual(task.cancellation_reason, data['cancellation_reason'])
        self.assertEqual(self.client.get('/api/tasks/?active=true').data, [])
        self.assertEqual(len(self.client.get('/api/tasks/').data), 1)
        self.assertEqual(self.client.delete(f'/api/tasks/{task.pk}/').status_code, 403)
        self.assertEqual(self.client.patch(f'/api/tasks/{task.pk}/', {'status': 'TODO', 'expected_version': 2}, format='json').status_code, 400)
        self.client.force_authenticate(self.foreign)
        self.assertEqual(self.client.patch(f'/api/tasks/{task.pk}/', data, format='json').status_code, 404)


class ConcurrentCancellationTests(TransactionTestCase):
    @skipUnless(connection.vendor == 'postgresql', 'Real PostgreSQL row locks required for concurrent cancellation')
    def test_two_writers_one_compensation(self):
        self.assertEqual(connection.vendor, 'postgresql')
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        owner = User.objects.create_user(username='parallel-cancel-owner')
        user = User.objects.create_user(username='parallel-cancel-operator', exploitation=owner.exploitation)
        MembershipPermission.objects.create(membership=user.memberships.get(), code='expenses.delete')
        species = Espece.objects.create(nom='Synthetic', exploitation=owner.exploitation)
        lot = Lot.objects.create(nom='Synthetic', exploitation=owner.exploitation, espece=species, date_debut='2026-10-12')
        expense = Depense.objects.create(lot=lot, montant='20.00')
        barrier = Barrier(2)
        def writer():
            close_old_connections()
            try:
                client = APIClient()
                client.force_authenticate(User.objects.get(pk=user.pk))
                barrier.wait(timeout=10)
                return client.post(f'/api/depenses/cancel/{expense.pk}/',
                    {'reason': 'Synthetic', 'operation_uuid': str(uuid4())}, format='json').status_code
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: writer(), range(2)))
        self.assertEqual(sorted(results), [200, 400])
        self.assertEqual(ExpenseCancellation.objects.count(), 1)
        self.assertTrue(Depense.history.filter(pk=expense.pk).exists())
