from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from core.models import Achat, Espece, Lot, Mouvement, PeseeProduction, User


class ProductionWeightsApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="eleveur-pesees")
        self.other = User.objects.create_user(username="autre-pesees")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        espece = Espece.objects.create(
            nom="Poulet", exploitation=self.user.exploitation,
        )
        self.lot = Lot.objects.create(
            nom="Chair septembre", espece=espece,
            exploitation=self.user.exploitation, date_debut=date(2026, 9, 1),
        )
        self.oeufs = Lot.objects.create(
            nom="Pondeuses", espece=espece,
            exploitation=self.user.exploitation, date_debut=date(2026, 9, 1),
            type_production="OEUFS",
        )
        Achat.objects.create(
            lot=self.lot, exploitation=self.user.exploitation, created_by=self.user,
            quantite=100, prix_total=Decimal("500"), prix_unitaire=Decimal("5"),
            date=date(2026, 9, 1),
        )
        Mouvement.objects.create(
            lot=self.lot, exploitation=self.user.exploitation, created_by=self.user,
            type_mouvement="MORTALITE", quantite=20, date=date(2026, 9, 2),
        )

    def payload(self, when, count=10, total="10.000", lot=None):
        return {
            "lot": (lot or self.lot).pk,
            "pesee_at": when.isoformat(),
            "nombre_animaux_peses": count,
            "poids_total_kg": total,
        }

    def test_average_gmq_current_count_and_biomass_are_calculated(self):
        first = datetime(2026, 9, 15, 9, 0, tzinfo=dt_timezone.utc)
        second = datetime(2026, 9, 22, 9, 0, tzinfo=dt_timezone.utc)
        for payload in (
            self.payload(first, total="10.000"),
            self.payload(second, total="17.500"),
        ):
            response = self.client.post(
                "/api/production/pesees/", payload, format="json",
            )
            self.assertEqual(response.status_code, 201, response.data)

        result = response.data
        self.assertEqual(result["effectif_actuel"], 80)
        self.assertEqual(result["dernier_poids_moyen_kg"], Decimal("1.750"))
        self.assertEqual(result["biomasse_estimee_kg"], Decimal("140.000"))
        self.assertEqual(result["gmq_g_par_jour"], 107.1)
        self.assertEqual(result["pesees"][0]["gmq_g_par_jour"], None)
        self.assertEqual(PeseeProduction.objects.count(), 2)

    def test_multiple_weighings_same_day_skip_gmq(self):
        day = datetime(2026, 9, 15, 9, 0, tzinfo=dt_timezone.utc)
        self.client.post(
            "/api/production/pesees/", self.payload(day), format="json",
        )
        response = self.client.post(
            "/api/production/pesees/",
            self.payload(day.replace(hour=17), total="12.000"), format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data["gmq_g_par_jour"])

    def test_biomass_uses_current_stock_after_a_movement(self):
        when = datetime(2026, 9, 15, 9, 0, tzinfo=dt_timezone.utc)
        response = self.client.post(
            "/api/production/pesees/", self.payload(when), format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["biomasse_estimee_kg"], Decimal("80.000"))

        Mouvement.objects.create(
            lot=self.lot, exploitation=self.user.exploitation, created_by=self.user,
            type_mouvement="VENTE", quantite=10, date=date(2026, 9, 16),
        )
        updated = self.client.get(f"/api/production/pesees/?lot={self.lot.pk}")
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["effectif_actuel"], 70)
        self.assertEqual(updated.data["biomasse_estimee_kg"], Decimal("70.000"))

    def test_invalid_counts_weights_other_tenant_and_non_chair_are_rejected(self):
        when = datetime(2026, 9, 15, 9, 0, tzinfo=dt_timezone.utc)
        for count, total in ((0, "10.000"), (-1, "10.000"), (81, "10.000"), (10, "0"), (10, "-1")):
            response = self.client.post(
                "/api/production/pesees/",
                self.payload(when, count=count, total=total), format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(PeseeProduction.objects.count(), 0)

        response = self.client.post(
            "/api/production/pesees/",
            self.payload(when, lot=self.oeufs), format="json",
        )
        self.assertEqual(response.status_code, 400)

        other_species = Espece.objects.create(
            nom="Poulet", exploitation=self.other.exploitation,
        )
        private_lot = Lot.objects.create(
            nom="Privé", espece=other_species,
            exploitation=self.other.exploitation, date_debut=date(2026, 9, 1),
        )
        response = self.client.post(
            "/api/production/pesees/",
            self.payload(when, lot=private_lot), format="json",
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(
            self.client.get(f"/api/production/pesees/?lot={private_lot.pk}").status_code,
            404,
        )

    def test_history_requires_lot_and_is_tenant_scoped(self):
        self.assertEqual(self.client.get("/api/production/pesees/").status_code, 400)
        self.assertEqual(
            self.client.get(f"/api/production/pesees/?lot={self.lot.pk}").status_code,
            200,
        )
