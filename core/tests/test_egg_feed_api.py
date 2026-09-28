from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Achat, CategorieDepense, Client, Depense, Espece, Lot, User


class EggFeedApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="aliment-oeufs", password="test-pass")
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.espece = Espece.objects.create(
            nom="Poulet",
            exploitation=self.user.exploitation,
        )
        self.lot = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Pondeuses aliment",
            date_debut=date(2026, 1, 1),
            type_production="OEUFS",
            statut_production="PONTE",
        )
        self.customer = Client.objects.create(
            nom="Acheteur",
            exploitation=self.user.exploitation,
        )
        Achat.objects.create(
            exploitation=self.user.exploitation,
            lot=self.lot,
            quantite=100,
            prix_total=Decimal("100000.00"),
            prix_unitaire=Decimal("1000.00"),
            date=date(2026, 1, 1),
            created_by=self.user,
        )
        self.category = CategorieDepense.objects.create(
            nom="Aliment test",
            exploitation=self.user.exploitation,
        )

    def test_feed_entry_can_use_estimated_unit_cost(self):
        response = self.api.post(
            "/api/oeufs/alimentation/",
            {
                "lot": self.lot.id,
                "date": timezone.localdate().isoformat(),
                "quantite_kg": "12.500",
                "prix_kg": "400.00",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(Decimal(response.data["cout_calcule"]), Decimal("5000.00"))

    def test_feed_entry_rejects_expense_from_another_lot(self):
        other_lot = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Autre lot",
            date_debut=date(2026, 1, 1),
            type_production="OEUFS",
        )
        expense = Depense.objects.create(
            lot=other_lot,
            categorie=self.category,
            date=timezone.localdate(),
            montant=Decimal("5000.00"),
        )

        response = self.api.post(
            "/api/oeufs/alimentation/",
            {
                "lot": self.lot.id,
                "date": timezone.localdate().isoformat(),
                "quantite_kg": "10.000",
                "depense": expense.id,
            },
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("depense", response.data)

    def test_statistics_do_not_double_count_linked_feed_expense(self):
        today = timezone.localdate()
        collection_response = self.api.post(
            "/api/oeufs/collectes/",
            {
                "lot": self.lot.id,
                "collecte_at": timezone.now().isoformat(),
                "nombre_collecte": 100,
                "nombre_casses": 0,
                "nombre_declasses": 0,
                "nombre_consommes_donnes": 0,
            },
            format="json",
        )
        self.assertEqual(collection_response.status_code, 201)
        sale_response = self.api.post(
            "/api/oeufs/ventes/",
            {
                "lot": self.lot.id,
                "client": self.customer.id,
                "date": today.isoformat(),
                "conditionnement": "PLATEAU",
                "nombre_conditionnements": 1,
                "prix_unitaire_conditionnement": "3000.00",
            },
            format="json",
        )
        self.assertEqual(sale_response.status_code, 201)

        linked_expense = Depense.objects.create(
            lot=self.lot,
            categorie=self.category,
            date=today,
            montant=Decimal("5000.00"),
        )
        linked_feed = self.api.post(
            "/api/oeufs/alimentation/",
            {
                "lot": self.lot.id,
                "date": today.isoformat(),
                "quantite_kg": "10.000",
                "prix_kg": "1000.00",
                "depense": linked_expense.id,
            },
            format="json",
        )
        self.assertEqual(linked_feed.status_code, 201)
        estimated_feed = self.api.post(
            "/api/oeufs/alimentation/",
            {
                "lot": self.lot.id,
                "date": today.isoformat(),
                "quantite_kg": "5.000",
                "prix_kg": "400.00",
            },
            format="json",
        )
        self.assertEqual(estimated_feed.status_code, 201)

        response = self.api.get(
            f"/api/oeufs/statistiques/?lot={self.lot.id}"
            f"&date_debut={today.isoformat()}&date_fin={today.isoformat()}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["oeufs_vendus"], 30)
        self.assertEqual(Decimal(response.data["chiffre_affaires_oeufs"]), Decimal("3000.00"))
        self.assertEqual(Decimal(response.data["consommation_aliment_kg"]), Decimal("15.000"))
        self.assertEqual(response.data["consommation_moyenne_par_poule_kg"], 0.15)
        self.assertEqual(Decimal(response.data["cout_alimentation"]), Decimal("7000.00"))
        self.assertEqual(Decimal(response.data["depenses_comptabilisees"]), Decimal("5000.00"))
        self.assertEqual(Decimal(response.data["couts_non_comptabilises"]), Decimal("2000.00"))
        self.assertEqual(response.data["cout_par_oeuf"], 70.0)
        self.assertEqual(Decimal(response.data["marge_oeufs"]), Decimal("-4000.00"))
