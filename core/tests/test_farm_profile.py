from django.test import TestCase
from rest_framework.test import APIClient
from core.models import User


class FarmProfileTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner-profile')
        self.operator = User.objects.create_user(username='operator-profile', exploitation=self.owner.exploitation)
        self.other = User.objects.create_user(username='other-profile')
        self.api = APIClient()

    def test_shared_name_and_tenant_isolation(self):
        self.api.force_authenticate(self.owner)
        result = self.api.patch('/api/farm-profile/', {'nom': ' Ferme La Gloire '}, format='json')
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.data['nom'], 'Ferme La Gloire')
        self.api.force_authenticate(self.operator)
        self.assertEqual(self.api.get('/api/farm-profile/').data, result.data)
        self.assertEqual(self.api.patch('/api/farm-profile/', {'nom': 'Autre'}, format='json').status_code, 403)
        self.api.force_authenticate(self.other)
        self.assertNotEqual(self.api.get('/api/farm-profile/').data['id'], result.data['id'])

    def test_invalid_names_and_anonymous_access(self):
        self.assertIn(self.api.get('/api/farm-profile/').status_code, (401, 403))
        self.api.force_authenticate(self.owner)
        for value in (' ', 'x' * 101):
            self.assertEqual(self.api.patch('/api/farm-profile/', {'nom': value}, format='json').status_code, 400)
