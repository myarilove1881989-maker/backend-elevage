from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from core.models import Client


class ClientLocationTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="client-location",
            email="client-location@example.com",
            password="MotDePasseSolide!45",
        )
        self.client.force_authenticate(self.user)

    def test_create_client_with_optional_country_and_city(self):
        response = self.client.post(
            "/api/clients/create/",
            {
                "nom": "Client test",
                "telephone": "0600000000",
                "pays": "cm",
                "ville": "Douala",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        created = Client.objects.get(pk=response.data["id"])
        self.assertEqual(created.pays, "CM")
        self.assertEqual(created.ville, "Douala")

    def test_location_fields_are_optional(self):
        response = self.client.post(
            "/api/clients/create/",
            {"nom": "Sans adresse", "telephone": "0700000000"},
            format="json",
        )
        self.assertEqual(response.status_code, 201)
