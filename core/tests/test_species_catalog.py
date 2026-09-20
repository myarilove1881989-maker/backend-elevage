from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from core.models import Espece
from core.species_catalog import SPECIES_CATALOG


class SpeciesCatalogTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="catalogue-eleveur",
            email="catalogue@example.com",
            password="MotDePasseSolide!45",
        )
        self.client.force_authenticate(self.user)

    def test_registration_creates_the_complete_catalog(self):
        response = self.client.post(
            "/api/register/",
            {
                "username": "nouveau-catalogue",
                "email": "nouveau-catalogue@example.com",
                "password": "MotDePasseSolide!67",
            },
            format="json",
        )
        self.assertEqual(response.status_code, 201)
        registered_user = get_user_model().objects.get(
            username="nouveau-catalogue"
        )
        names = set(
            Espece.objects.filter(exploitation=registered_user.exploitation)
            .values_list("nom", flat=True)
        )
        self.assertEqual(names, set(SPECIES_CATALOG))
        self.assertIn("Œufs", names)

    def test_species_endpoint_restores_missing_catalog_entries(self):
        Espece.objects.filter(
            exploitation=self.user.exploitation,
            nom="Poulet",
        ).delete()

        response = self.client.get("/api/especes/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["nom"] for item in response.data[: len(SPECIES_CATALOG)]],
            list(SPECIES_CATALOG),
        )

    def test_custom_species_is_rejected(self):
        response = self.client.post(
            "/api/especes/",
            {"nom": "Poule exotique"},
            format="json",
        )
        self.assertEqual(response.status_code, 400)
