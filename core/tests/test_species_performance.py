from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from rest_framework.test import APITestCase

from core.models import Achat, Depense, Espece, Lot, Vente


class SpeciesPerformanceTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="performance-eleveur",
            email="performance@example.com",
            password="MotDePasseSolide!45",
        )
        self.client.force_authenticate(self.user)
        self.exploitation = self.user.exploitation

    def create_lot(self, species_name, lot_name):
        species, _ = Espece.objects.get_or_create(
            exploitation=self.exploitation,
            nom=species_name,
        )
        return Lot.objects.create(
            exploitation=self.exploitation,
            espece=species,
            nom=lot_name,
            date_debut=date(2026, 1, 1),
            created_by=self.user,
        )

    def test_ranks_species_and_returns_best_sales_month(self):
        chicken = self.create_lot("Poulet", "Poulets janvier")
        pig = self.create_lot("Porc", "Porcs janvier")

        Achat.objects.create(
            exploitation=self.exploitation,
            lot=chicken,
            quantite=100,
            prix_total=Decimal("1000"),
            prix_unitaire=Decimal("10"),
            date=date(2026, 1, 1),
            created_by=self.user,
        )
        Achat.objects.create(
            exploitation=self.exploitation,
            lot=pig,
            quantite=20,
            prix_total=Decimal("1000"),
            prix_unitaire=Decimal("50"),
            date=date(2026, 1, 1),
            created_by=self.user,
        )
        Vente.objects.create(
            lot=chicken,
            quantite=20,
            prix_unitaire=Decimal("100"),
            date=date(2026, 1, 15),
        )
        Vente.objects.create(
            lot=chicken,
            quantite=50,
            prix_unitaire=Decimal("100"),
            date=date(2026, 2, 15),
        )
        Vente.objects.create(
            lot=pig,
            quantite=10,
            prix_unitaire=Decimal("200"),
            date=date(2026, 3, 15),
        )

        response = self.client.get("/api/performance-especes/")

        self.assertEqual(response.status_code, 200)
        ranking = response.data["classement"]
        self.assertEqual(ranking[0]["espece"], "Poulet")
        self.assertEqual(ranking[0]["marge"], 7000.0)
        self.assertEqual(ranking[0]["taux_ecoulement"], 70.0)
        self.assertEqual(ranking[0]["meilleur_mois"], "2026-02")
        self.assertEqual(ranking[0]["meilleur_mois_quantite"], 50)
        self.assertEqual(len(response.data["ventes_mensuelles"]), 3)

    def test_species_filter_keeps_only_requested_species(self):
        chicken = self.create_lot("Poulet", "Poulets")
        pig = self.create_lot("Porc", "Porcs")

        response = self.client.get(
            f"/api/performance-especes/?espece={pig.espece_id}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data["classement"]), 1)
        self.assertEqual(response.data["classement"][0]["espece"], "Porc")
        self.assertEqual(chicken.espece.nom, "Poulet")
