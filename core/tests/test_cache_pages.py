from datetime import date
from django.test import TestCase
from rest_framework.test import APIClient
from core.models import User, Client, Espece, Lot, Mouvement, Task


class OfflineCachePageTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='cache-owner')
        self.other = User.objects.create_user(username='cache-other')
        self.operator = User.objects.create_user(username='cache-operator', exploitation=self.owner.exploitation)
        self.api = APIClient()
        self.api.force_authenticate(self.owner)

    def test_pagination_is_bounded_stable_and_tenant_scoped(self):
        clients = [Client.objects.create(nom=f'C{i}', exploitation=self.owner.exploitation) for i in range(3)]
        Client.objects.create(nom='Secret other farm', exploitation=self.other.exploitation)
        response = self.api.get('/api/cache-page/', {'collection': 'clients', 'limit': 2})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['id'] for r in response.data['results']], [c.pk for c in clients[:2]])
        response = self.api.get('/api/cache-page/', {'collection': 'clients', 'limit': 2, 'after': response.data['next_cursor']})
        self.assertEqual([r['id'] for r in response.data['results']], [clients[2].pk])
        self.assertIsNone(response.data['next_cursor'])

    def test_invalid_limits_and_collection_are_rejected(self):
        for params in ({'collection': 'clients', 'limit': 10000}, {'collection': 'users'},
                       {'collection': 'clients', 'after': -1}):
            self.assertEqual(self.api.get('/api/cache-page/', params).status_code, 400)

    def test_operator_receives_only_general_and_personally_assigned_agenda(self):
        tasks = [Task.objects.create(exploitation=self.owner.exploitation, title='General', date=date.today()),
                 Task.objects.create(exploitation=self.owner.exploitation, title='Mine', date=date.today(), assigned_to=self.operator)]
        Task.objects.create(exploitation=self.owner.exploitation, title='Other', date=date.today(), assigned_to=self.owner)
        self.api.force_authenticate(self.operator)
        response = self.api.get('/api/cache-page/', {'collection': 'tasks'})
        self.assertEqual([r['id'] for r in response.data['results']], [task.pk for task in tasks])

    def test_disabled_member_has_no_cache_access(self):
        member = self.operator.memberships.get()
        member.is_active = False
        member.save()
        self.api.force_authenticate(self.operator)
        self.assertEqual(self.api.get('/api/cache-page/', {'collection': 'clients'}).status_code, 403)

    def test_lot_stock_is_confirmed_and_has_bounded_query_count(self):
        species = Espece.objects.create(nom='Test', exploitation=self.owner.exploitation)
        for i in range(20):
            lot = Lot.objects.create(nom=f'Lot{i}', exploitation=self.owner.exploitation,
                                     espece=species, date_debut=date.today())
            Mouvement.objects.create(lot=lot, type_mouvement='ACHAT', quantite=10, date=date.today())
        # Membership, locked farm, one stock query and atomic savepoint pair.
        with self.assertNumQueries(5):
            response = self.api.get('/api/cache-page/', {'collection': 'lots'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 20)
        self.assertTrue(all(row['stock'] == 10 for row in response.data['results']))

    def test_cache_endpoint_is_read_only(self):
        self.assertEqual(self.api.post('/api/cache-page/', {'collection': 'clients'}).status_code, 405)
