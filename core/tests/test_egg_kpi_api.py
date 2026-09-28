from datetime import date, datetime
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.egg_services import affect_egg_exit
from core.models import (
    Achat, CollecteOeufs, ConsommationAliment, Espece, Lot, Mouvement,
    MouvementOeufs, User,
)


TODAY = date(2026, 9, 28)
PARIS = ZoneInfo("Europe/Paris")


@override_settings(TIME_ZONE="Europe/Paris")
class EggKpiApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="kpi-oeufs")
        self.other = User.objects.create_user(username="kpi-autre")
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        species = Espece.objects.create(nom="Poulet", exploitation=self.user.exploitation)
        self.lot = Lot.objects.create(
            nom="Pondeuses Test", date_debut=date(2026, 1, 1),
            espece=species, exploitation=self.user.exploitation,
            type_production="OEUFS", statut_production="PONTE",
        )
        self.other_lot = Lot.objects.create(
            nom="Autres pondeuses", date_debut=date(2026, 1, 1),
            espece=species, exploitation=self.user.exploitation,
            type_production="OEUFS",
        )
        self.purchase = Achat.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            date=date(2026, 1, 1), quantite=1000,
            prix_total=Decimal("100000"), prix_unitaire=Decimal("100"),
        )

    @staticmethod
    def at(day, hour, minute=0):
        return datetime(2026, 9, day, hour, minute, tzinfo=PARIS)

    def collect(self, count, *, day=28, hour=8, minute=0, **losses):
        response = self.api.post("/api/oeufs/collectes/", {
            "lot": self.lot.pk,
            "collecte_at": self.at(day, hour, minute).isoformat(),
            "nombre_collecte": count,
            **losses,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def distribute(self, quantity, *, day=28, hour=8):
        return ConsommationAliment.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            date=date(2026, 9, day), distribution_at=self.at(day, hour),
            aliment="Pondeuse", quantite_kg=Decimal(quantity),
        )

    def kpi(self, query=""):
        with patch("core.egg_kpis.timezone.localdate", return_value=TODAY):
            response = self.api.get(f"/api/oeufs/kpi/?lot={self.lot.pk}{query}")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_single_and_three_collections_with_all_initial_losses(self):
        self.collect(600, hour=8, nombre_casses=10, nombre_declasses=4,
                     nombre_consommes_donnes=6)
        first = self.kpi()
        self.assertEqual(first["production_jour"], 600)
        self.assertEqual(first["commercialisable_jour"], 580)
        self.collect(550, hour=13, nombre_casses=13)
        self.collect(470, hour=17)
        result = self.kpi()
        self.assertEqual(result["date"], TODAY)
        self.assertEqual(result["effectif_actuel"], 1000)
        self.assertEqual(result["production_jour"], 1620)
        self.assertEqual(result["commercialisable_jour"], 1587)
        self.assertEqual(result["casses_jour"], 23)
        self.assertEqual(result["taux_ponte"], 162.0)
        self.assertEqual(result["taux_casse"], 1.4)
        self.assertTrue(result["taux_ponte_inhabituel"])
        self.assertEqual(result["evolution"][-1]["production"], 1620)

    def test_local_standard_scenario_through_existing_collection_api(self):
        self.collect(450, hour=8, nombre_casses=4)
        self.collect(310, hour=13, nombre_casses=3)
        self.collect(170, hour=17, nombre_casses=3)
        self.distribute("55.000", hour=8)
        self.distribute("60.000", hour=17)
        result = self.kpi()
        self.assertEqual(result["production_jour"], 930)
        self.assertEqual(result["commercialisable_jour"], 920)
        self.assertEqual(result["stock_disponible"], 920)
        self.assertEqual(result["taux_ponte"], 93.0)
        self.assertEqual(result["taux_casse"], 1.1)
        self.assertEqual(result["aliment_jour_kg"], 115)
        self.assertEqual(result["consommation_par_poule_g"], 115)

    def test_yesterday_and_other_lot_do_not_affect_today(self):
        self.collect(200, day=27)
        self.collect(850)
        CollecteOeufs.objects.create(
            lot=self.other_lot, exploitation=self.user.exploitation,
            collecte_at=self.at(28, 9), nombre_collecte=500,
        )
        result = self.kpi()
        self.assertEqual(result["production_jour"], 850)
        self.assertEqual(result["taux_ponte"], 85.0)
        self.assertEqual(result["stock_disponible"], 1050)

    def test_no_collection_no_feed_and_zero_birds_have_distinct_nulls(self):
        empty = self.kpi()
        self.assertEqual(empty["effectif_actuel"], 1000)
        self.assertFalse(empty["collectes_enregistrees"])
        self.assertFalse(empty["aliment_enregistre"])
        for key in ("production_jour", "commercialisable_jour", "taux_ponte",
                    "taux_casse", "aliment_jour_kg", "consommation_par_poule_g"):
            self.assertIsNone(empty[key], key)
        self.assertEqual(empty["stock_disponible"], 0)

        self.purchase.delete()
        self.collect(105)
        self.distribute("5.000")
        zero = self.kpi()
        self.assertEqual(zero["effectif_actuel"], 0)
        self.assertEqual(zero["production_jour"], 105)
        self.assertIsNone(zero["taux_ponte"])
        self.assertIsNone(zero["consommation_par_poule_g"])
        self.assertEqual(zero["aliment_jour_kg"], 5)

    def test_recorded_zero_collection_has_zero_rate_but_no_breakage_ratio(self):
        # L'API refuse désormais zéro, mais une ancienne ligne peut exister.
        CollecteOeufs.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            collecte_at=self.at(28, 8), nombre_collecte=0,
        )
        result = self.kpi()
        self.assertTrue(result["collectes_enregistrees"])
        self.assertEqual(result["production_jour"], 0)
        self.assertEqual(result["commercialisable_jour"], 0)
        self.assertEqual(result["taux_ponte"], 0)
        self.assertIsNone(result["taux_casse"])

    def test_unusual_rate_is_reported_without_capping(self):
        self.purchase.quantite = 100
        self.purchase.save(update_fields=["quantite"])
        self.collect(105)
        result = self.kpi()
        self.assertEqual(result["taux_ponte"], 105.0)
        self.assertTrue(result["taux_ponte_inhabituel"])

    def test_mortality_today_uses_documented_current_count_approximation(self):
        self.collect(450, hour=8)
        Mouvement.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            date=TODAY, type_mouvement="MORTALITE", quantite=10,
        )
        self.collect(430, hour=17)
        result = self.kpi()
        self.assertEqual(result["effectif_actuel"], 990)
        self.assertEqual(result["effectif_reference"], 990)
        self.assertEqual(result["effectif_reference_mode"],
                         "effectif_actuel_approximation")
        self.assertEqual(result["production_jour"], 880)
        self.assertEqual(result["taux_ponte"], 88.9)

    def test_feed_uses_distributed_kg_and_same_population_reference(self):
        self.distribute("50.000", hour=8)
        self.distribute("60.000", hour=17)
        self.distribute("100.000", day=27)
        ConsommationAliment.objects.create(
            lot=self.other_lot, exploitation=self.user.exploitation,
            date=TODAY, quantite_kg=Decimal("1000.000"),
        )
        result = self.kpi()
        self.assertTrue(result["aliment_enregistre"])
        self.assertEqual(result["aliment_jour_kg"], 110)
        self.assertEqual(result["consommation_par_poule_g"], 110)

    def test_stock_matches_phase_six_even_when_origin_is_unknown(self):
        collection = self.collect(100)
        loss = MouvementOeufs.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            type_mouvement="CASSE", quantite=40, date=self.at(28, 18),
        )
        self.assertEqual(self.kpi()["stock_disponible"], 60)
        response = self.api.get(f"/api/oeufs/stock-date/?lot={self.lot.pk}")
        self.assertEqual(response.data["stock_global"], 60)
        self.assertIsNone(response.data["collectes"][0]["restant"])
        affect_egg_exit(loss, [{"collecte": collection, "quantite": 40}])
        self.assertEqual(self.kpi()["stock_disponible"], 60)
        response = self.api.get(f"/api/oeufs/stock-date/?lot={self.lot.pk}")
        self.assertEqual(response.data["collectes"][0]["restant"], 60)

    def test_evolution_preserves_missing_days_and_groups_same_day(self):
        self.collect(800, day=25)
        self.collect(500, day=26, hour=8)
        self.collect(350, day=26, hour=16)
        self.collect(900, day=28)
        result = self.kpi("&date_debut=2026-09-25&date_fin=2026-09-28")
        self.assertEqual([row["production"] for row in result["evolution"]],
                         [800, 850, None, 900])
        self.assertEqual([row["date"] for row in result["evolution"]],
                         [date(2026, 9, day) for day in (25, 26, 27, 28)])
        self.assertEqual(result["taux_ponte"], 90.0)

    def test_paris_local_midnight_and_end_of_day(self):
        for instant in (
            "2026-09-27T22:00:00+00:00",  # 00:00 Paris, jour du KPI
            "2026-09-28T21:59:00+00:00",  # 23:59 Paris
            "2026-09-27T21:59:00+00:00",  # veille à Paris
            "2026-09-28T22:00:00+00:00",  # lendemain à Paris
        ):
            response = self.api.post("/api/oeufs/collectes/", {
                "lot": self.lot.pk, "collecte_at": instant, "nombre_collecte": 10,
            }, format="json")
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.kpi()["production_jour"], 20)

    def test_tenant_isolation_type_and_period_validation(self):
        self.collect(90)
        other_api = APIClient()
        other_api.force_authenticate(self.other)
        self.assertEqual(other_api.get(f"/api/oeufs/kpi/?lot={self.lot.pk}").status_code, 404)
        self.assertEqual(self.kpi()["production_jour"], 90)
        chair = Lot.objects.create(
            nom="Chair", date_debut=date(2026, 1, 1),
            espece=self.lot.espece, exploitation=self.user.exploitation,
            type_production="CHAIR",
        )
        self.assertEqual(self.api.get(f"/api/oeufs/kpi/?lot={chair.pk}").status_code, 404)
        self.assertEqual(self.api.get(f"/api/oeufs/kpi/?lot={self.lot.pk}&date_debut=2026-09-27").status_code, 400)
        self.assertEqual(self.api.get(f"/api/oeufs/kpi/?lot={self.lot.pk}&date_debut=2026-09-30&date_fin=2026-09-28").status_code, 400)
