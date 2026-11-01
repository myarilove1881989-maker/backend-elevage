from datetime import date
from decimal import Decimal
from uuid import uuid4
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User, Client, Espece, Lot, Vente, Payment, Task
from core.views import get_client_balance


class ClientBalanceRegressionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='balance-owner')
        self.other = User.objects.create_user(username='balance-other')
        self.api = APIClient()
        self.api.force_authenticate(self.owner)
        self.species = Espece.objects.create(nom='Poules', exploitation=self.owner.exploitation)
        self.lot = Lot.objects.create(nom='Lot', espece=self.species,
            exploitation=self.owner.exploitation, date_debut=date(2026, 1, 1))
        self.customer = Client.objects.create(nom='Acheteur', exploitation=self.owner.exploitation)

    def sale(self, customer=None, amount='100.00'):
        return Vente.objects.create(client=customer or self.customer, lot=self.lot,
            quantite=1, prix_unitaire=Decimal(amount))

    def pay(self, amount, farm=None):
        return Payment.objects.create(client=self.customer,
            exploitation=farm or self.owner.exploitation, montant=Decimal(amount), date=date.today())

    def test_credit_partial_and_settled_are_all_visible_without_multiplied_totals(self):
        self.sale(); self.sale(amount='50.00')
        row = self.api.get('/api/dettes-clients/').data[0]
        self.assertEqual((row['total_facture'], row['total_paye'], row['reste'], row['statut']),
                         (150, 0, 150, 'IMPAYE'))
        self.pay('20'); self.pay('30')
        row = self.api.get('/api/dettes-clients/').data[0]
        self.assertEqual((row['total_facture'], row['total_paye'], row['reste'], row['statut']),
                         (150, 50, 100, 'PARTIEL'))
        self.pay('100')
        row = self.api.get('/api/dettes-clients/').data[0]
        self.assertEqual((row['reste'], row['statut']), (0, 'PAYE'))
        self.assertEqual(get_client_balance(self.customer), 0)

    def test_cross_farm_and_voided_facts_do_not_leak(self):
        self.sale(); self.pay('25')
        cancelled = self.sale(amount='999.00')
        Vente.history.filter(pk=cancelled.pk).update(voided_at=timezone.now(), void_decision_uuid=uuid4())
        outsider = Client.objects.create(nom='Secret', exploitation=self.other.exploitation)
        self.sale(customer=outsider, amount='888')  # deliberately inconsistent historical row
        self.pay('777', farm=self.other.exploitation)
        response = self.api.get('/api/dettes-clients/')
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['total_facture'], 100)
        self.assertEqual(response.data[0]['reste'], 75)
        self.assertEqual(get_client_balance(self.customer), 75)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.get('/api/dettes-clients/').data, [])

    def test_read_does_not_invent_sales_from_unlinked_movements(self):
        from core.models import Mouvement
        Mouvement.objects.create(lot=self.lot, exploitation=self.owner.exploitation,
            client=self.customer, type_mouvement='VENTE', quantite=2, prix_unitaire=10)
        self.assertEqual(self.api.get('/api/dettes-clients/').data, [])
        self.assertEqual(Vente.objects.count(), 0)


class AgendaRepeatTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='repeat-owner')
        self.operator = User.objects.create_user(username='repeat-operator', exploitation=self.owner.exploitation)
        self.other = User.objects.create_user(username='repeat-other')
        self.api = APIClient(); self.api.force_authenticate(self.owner)
        self.payload = dict(title='Soins', start_date='2026-11-02', end_date='2026-11-08',
                            weekdays=[1,3,5], assigned_to=self.operator.pk)

    def post(self, **changes):
        return self.api.post('/api/tasks/repeat/', dict(self.payload, **changes), format='json')

    def test_selected_days_and_retry_preserve_existing_progress(self):
        result = self.post()
        self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual(result.data['created'], 3)
        self.assertEqual(list(Task.objects.order_by('date').values_list('date', flat=True)),
                         [date(2026,11,2), date(2026,11,4), date(2026,11,6)])
        first = Task.objects.order_by('date').first()
        first.status='DONE'; first.report='Fait'; first.save()
        again = self.post()
        self.assertEqual((again.data['created'], again.data['skipped']), (0,3))
        first.refresh_from_db(); self.assertEqual(first.report, 'Fait')
        self.assertEqual(Task.objects.count(), 3)
        listed=self.api.get('/api/tasks/?active=true')
        self.assertEqual(listed.status_code,200)
        self.assertEqual(len(listed.data),3)

    def test_daily_and_inclusive_bounds(self):
        result=self.post(weekdays=[1,2,3,4,5,6,7], end_date='2026-11-04')
        self.assertEqual(result.data['created'],3)

    def test_invalid_dates_days_or_assignee_create_nothing(self):
        for changes in [dict(end_date='2026-11-01'), dict(end_date='2028-11-02'),
                        dict(weekdays=[]), dict(weekdays=[8]),
                        dict(start_date='2026-11-03',end_date='2026-11-03',weekdays=[1]),
                        dict(assigned_to=self.other.pk), dict(assigned_to=self.owner.pk)]:
            result=self.post(**changes)
            self.assertEqual(result.status_code,400,result.data)
            self.assertFalse(Task.objects.exists())

    def test_operator_cannot_plan_and_disabled_membership_is_denied(self):
        self.api.force_authenticate(self.operator)
        self.assertEqual(self.post().status_code,403)
        self.owner.is_active=False; self.owner.save(update_fields=['is_active'])
        self.api.force_authenticate(self.owner)
        self.assertEqual(self.post().status_code,403)
        self.assertFalse(Task.objects.exists())
