from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.egg_services import affect_egg_exit
from core.models import (
    AffectationMouvementOeufs, Client, Espece, Lot, MouvementOeufs,
    User, Vente, VenteOeufs,
)


class DatedEggStockTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="stock-date", password="secret")
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        species = Espece.objects.create(nom="Poule", exploitation=self.user.exploitation)
        self.lot = Lot.objects.create(
            exploitation=self.user.exploitation, espece=species,
            nom="Pondeuses Test", date_debut=date(2026, 1, 1),
            type_production="OEUFS", statut_production="PONTE",
        )
        self.customer = Client.objects.create(
            nom="Client œufs", exploitation=self.user.exploitation,
        )

    @staticmethod
    def at(day, hour):
        return timezone.make_aware(datetime(2026, 9, day, hour))

    def collect(self, day, hour, total, losses=0):
        response = self.api.post("/api/oeufs/collectes/", {
            "lot": self.lot.id, "collecte_at": self.at(day, hour).isoformat(),
            "nombre_collecte": total, "nombre_casses": losses,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def stock(self):
        response = self.api.get(f"/api/oeufs/stock-date/?lot={self.lot.id}")
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def sell(self, count, allocations=None):
        payload = {
            "lot": self.lot.id, "client": self.customer.id,
            "date": "2026-09-28", "conditionnement": "UNITE",
            "nombre_conditionnements": count,
            "prix_unitaire_conditionnement": "100.00",
        }
        if allocations is not None:
            payload["affectations"] = allocations
        return self.api.post("/api/oeufs/ventes/", payload, format="json")

    def test_three_collections_same_day_stock_scenario_and_explicit_exit(self):
        a = self.collect(26, 8, 900, 20)
        b = self.collect(27, 8, 1210, 10)
        c = self.collect(27, 17, 605, 5)

        before = self.stock()
        self.assertEqual(before["stock_global"], 2680)
        self.assertTrue(before["origines_completes"])
        self.assertEqual([row["id"] for row in before["collectes"]], [c, b, a])
        self.assertEqual([row["restant"] for row in before["collectes"]], [600, 1200, 880])
        self.assertEqual(sum(row["restant"] for row in before["collectes"]), 2680)

        sale = self.sell(480, [{"collecte": a, "quantite": 480}])
        self.assertEqual(sale.status_code, 201, sale.data)
        after = self.stock()
        self.assertEqual(after["stock_global"], 2200)
        self.assertTrue(after["origines_completes"])
        self.assertEqual([row["restant"] for row in after["collectes"]], [600, 1200, 400])
        self.assertEqual(sum(row["restant"] for row in after["collectes"]), 2200)
        self.assertEqual(AffectationMouvementOeufs.objects.count(), 1)

    def test_multiple_allocations_and_exhausted_collection_remain_visible(self):
        a = self.collect(26, 8, 100)
        b = self.collect(26, 13, 200)
        c = self.collect(26, 17, 30)
        sale = self.sell(130, [
            {"collecte": a, "quantite": 100},
            {"collecte": c, "quantite": 30},
        ])
        self.assertEqual(sale.status_code, 201, sale.data)
        state = self.stock()
        self.assertEqual([row["id"] for row in state["collectes"]], [c, b, a])
        self.assertEqual([row["restant"] for row in state["collectes"]], [0, 200, 0])
        self.assertEqual(state["stock_global"], 200)

    def test_legacy_sale_keeps_global_stock_but_not_fictional_origins(self):
        self.collect(26, 8, 500)
        self.collect(27, 8, 700)
        # Écriture Phase 8 historique : elle ne passe pas par le nouveau FIFO.
        vente = Vente.objects.create(
            lot=self.lot, client=self.customer, date=date(2026, 9, 28),
            quantite=600, prix_unitaire=Decimal("100.00"),
        )
        sale = VenteOeufs.objects.create(
            vente=vente, exploitation=self.user.exploitation, lot=self.lot,
            conditionnement="UNITE", nombre_conditionnements=600,
            oeufs_par_conditionnement=1, nombre_oeufs=600,
            prix_unitaire_conditionnement=Decimal("100.00"),
            montant_total=Decimal("60000.00"),
        )
        MouvementOeufs.objects.create(
            exploitation=self.user.exploitation, lot=self.lot,
            type_mouvement="VENTE", quantite=600,
            vente_oeufs=sale, date=self.at(28, 12),
        )
        state = self.stock()
        self.assertEqual(state["stock_global"], 600)
        self.assertEqual(state["sorties_non_attribuees"], 600)
        self.assertFalse(state["origines_completes"])
        self.assertEqual([row["restant"] for row in state["collectes"]], [None, None])
        self.assertEqual(VenteOeufs.objects.count(), 1)

        deletion = self.api.delete(f"/api/oeufs/ventes/{sale.pk}/")
        self.assertEqual(deletion.status_code, 204)
        self.assertEqual(self.stock()["stock_global"], 1200)
        self.assertTrue(self.stock()["origines_completes"])

    def test_historical_production_and_loss_without_origin_are_visible_as_uncertain(self):
        self.collect(26, 8, 100)
        MouvementOeufs.objects.create(
            exploitation=self.user.exploitation, lot=self.lot,
            type_mouvement="PRODUCTION", quantite=40,
        )
        loss = MouvementOeufs.objects.create(
            exploitation=self.user.exploitation, lot=self.lot,
            type_mouvement="CASSE", quantite=20,
        )
        state = self.stock()
        self.assertEqual(state["stock_global"], 120)
        self.assertEqual(state["entrees_hors_collecte"], 40)
        self.assertEqual(state["sorties_non_attribuees"], 20)
        self.assertIsNone(state["collectes"][0]["restant"])
        # L'ancienne écriture de casse ne devient pas artificiellement traçable.
        self.assertEqual(loss.affectations.count(), 0)

    def test_post_storage_loss_can_be_attributed_without_changing_ledger(self):
        collection = self.collect(26, 8, 100)
        loss = MouvementOeufs.objects.create(
            exploitation=self.user.exploitation, lot=self.lot,
            type_mouvement="CASSE", quantite=20,
            date=self.at(27, 8),
        )
        affect_egg_exit(loss, [{"collecte": collection, "quantite": 20}])
        state = self.stock()
        self.assertEqual(state["stock_global"], 80)
        self.assertTrue(state["origines_completes"])
        self.assertEqual(state["collectes"][0]["restant"], 80)

    def test_correction_and_deletion_protect_used_collection(self):
        collection = self.collect(26, 8, 100)
        self.assertEqual(self.sell(80, [
            {"collecte": collection, "quantite": 80},
        ]).status_code, 201)
        url = f"/api/oeufs/collectes/{collection}/"
        self.assertEqual(self.api.patch(url, {"nombre_collecte": 70}, format="json").status_code, 400)
        self.assertEqual(self.api.patch(url, {
            "collecte_at": self.at(27, 8).isoformat(),
        }, format="json").status_code, 400)
        self.assertEqual(self.api.delete(url).status_code, 400)
        self.assertEqual(self.api.patch(url, {"nombre_collecte": 80}, format="json").status_code, 200)
        self.assertEqual(self.stock()["collectes"][0]["restant"], 0)
        self.assertEqual(self.stock()["stock_global"], 0)

    def test_deleting_unpaid_allocated_sale_restores_original_collection(self):
        collection = self.collect(26, 8, 100)
        sale = self.sell(100, [{"collecte": collection, "quantite": 100}])
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual(self.stock()["collectes"][0]["restant"], 0)
        response = self.api.delete(f"/api/oeufs/ventes/{sale.data['id']}/")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(AffectationMouvementOeufs.objects.count(), 0)
        self.assertEqual(self.stock()["collectes"][0]["restant"], 100)
        self.assertEqual(self.stock()["stock_global"], 100)

    def test_mismatched_production_entries_never_claim_exact_origins(self):
        a = self.collect(26, 8, 100)
        b = self.collect(27, 8, 100)
        first = MouvementOeufs.objects.get(collecte_id=a)
        second = MouvementOeufs.objects.get(collecte_id=b)
        first.quantite = 90
        first.save()
        second.quantite = 110
        second.save()
        state = self.stock()
        self.assertEqual(state["stock_global"], 200)
        self.assertFalse(state["origines_completes"])
        self.assertEqual([row["restant"] for row in state["collectes"]], [None, None])

    def test_invalid_allocation_rolls_back_sale_and_foreign_source_is_rejected(self):
        collection = self.collect(26, 8, 100)
        self.collect(27, 8, 100)
        response = self.sell(101, [{"collecte": collection, "quantite": 101}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(VenteOeufs.objects.count(), 0)
        response = self.sell(30, [{"collecte": collection, "quantite": 20}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(VenteOeufs.objects.count(), 0)

        other = User.objects.create_user(username="other-stock", password="secret")
        species = Espece.objects.create(nom="Poule B", exploitation=other.exploitation)
        other_lot = Lot.objects.create(
            exploitation=other.exploitation, espece=species, nom="Ponte B",
            date_debut=date(2026, 1, 1), type_production="OEUFS", statut_production="PONTE",
        )
        other_api = APIClient()
        other_api.force_authenticate(other)
        foreign_collection = other_api.post("/api/oeufs/collectes/", {
            "lot": other_lot.id, "collecte_at": self.at(26, 8).isoformat(),
            "nombre_collecte": 50,
        }, format="json")
        self.assertEqual(foreign_collection.status_code, 201)
        response = self.sell(30, [{"collecte": foreign_collection.data["id"], "quantite": 30}])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(VenteOeufs.objects.count(), 0)
        self.assertEqual(self.stock()["stock_global"], 200)

        other_api.force_authenticate(other)
        self.assertEqual(other_api.get(f"/api/oeufs/stock-date/?lot={self.lot.id}").status_code, 404)
        self.assertEqual(other_api.get(f"/api/oeufs/collectes/{collection}/").status_code, 404)
        self.assertEqual(other_api.patch(f"/api/oeufs/collectes/{collection}/", {
            "nombre_collecte": 1,
        }, format="json").status_code, 404)
        self.assertEqual(other_api.delete(f"/api/oeufs/collectes/{collection}/").status_code, 404)
