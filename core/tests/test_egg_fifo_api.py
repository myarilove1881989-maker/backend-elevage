from datetime import datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.egg_services import affect_egg_exit, get_dated_egg_stock
from core.models import (
    AffectationMouvementOeufs, Client, CollecteOeufs, Espece, Lot, MouvementOeufs,
    User, Vente, VenteOeufs,
)


class EggFifoApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="fifo-ponte")
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        species = Espece.objects.create(nom="Poule", exploitation=self.user.exploitation)
        self.lot = Lot.objects.create(
            nom="Pondeuses FIFO Test", espece=species,
            exploitation=self.user.exploitation, date_debut=timezone.localdate(),
            type_production="OEUFS", statut_production="PONTE",
        )
        self.client = Client.objects.create(
            nom="Client FIFO", exploitation=self.user.exploitation,
        )

    def at(self, days_ago, hour=8):
        return timezone.make_aware(datetime.combine(
            timezone.localdate() - timedelta(days=days_ago), time(hour),
        ))

    def collect(self, quantity, *, days_ago=1, hour=8, broken=0,
                downgraded=0, consumed=0, lot=None):
        lot = lot or self.lot
        response = self.api.post("/api/oeufs/collectes/", {
            "lot": lot.pk, "collecte_at": self.at(days_ago, hour).isoformat(),
            "nombre_collecte": quantity, "nombre_casses": broken,
            "nombre_declasses": downgraded,
            "nombre_consommes_donnes": consumed,
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        return response.data["id"]

    def sell(self, quantity, *, days_ago=0, lot=None, client=None, **extra):
        payload = {
            "lot": (lot or self.lot).pk, "client": (client or self.client).pk,
            "conditionnement": "UNITE", "nombre_conditionnements": quantity,
            "prix_unitaire_conditionnement": "100.00",
            "date": (timezone.localdate() - timedelta(days=days_ago)).isoformat(),
        }
        payload.update(extra)
        return self.api.post("/api/oeufs/ventes/", payload, format="json")

    def stock(self, lot=None):
        return get_dated_egg_stock(self.user.exploitation, lot or self.lot)

    def test_single_collection_and_initial_losses(self):
        source = self.collect(215, broken=5, downgraded=5, consumed=5)
        sale = self.sell(100)
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual([(row["collecte"], row["quantite"])
                          for row in sale.data["origines_stock"]], [(source, 100)])
        state = self.stock()
        self.assertTrue(state["origines_completes"])
        self.assertEqual(state["collectes"][0]["nombre_commercialisable"], 200)
        self.assertEqual(state["collectes"][0]["restant"], 100)
        self.assertEqual(state["stock_global"], 100)

    def test_608_then_300_then_insufficient_1400_is_one_sale_per_request(self):
        a = self.collect(120, days_ago=3)
        b = self.collect(700, days_ago=2)
        c = self.collect(1400, days_ago=1)
        first = self.api.post("/api/oeufs/ventes/", {
            "lot": self.lot.pk, "client": self.client.pk,
            "conditionnement": "COMPOSE", "nombre_alveoles": 20,
            "oeufs_supplementaires": 8, "prix_total": "60800.00",
            "date": timezone.localdate().isoformat(),
        }, format="json")
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data["nombre_oeufs"], 608)
        self.assertEqual([(row["collecte"], row["quantite"])
                          for row in first.data["origines_stock"]], [(a, 120), (b, 488)])
        state = self.stock()
        self.assertEqual(state["stock_global"], 1612)
        self.assertEqual({row["id"]: row["restant"] for row in state["collectes"]},
                         {a: 0, b: 212, c: 1400})
        detail = self.api.get(f"/api/oeufs/ventes/{first.data['id']}/")
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.data["origines_stock"], first.data["origines_stock"])
        self.assertEqual(Vente.objects.count(), 1)
        self.assertEqual(Vente.objects.get().montant_total, Decimal("60800.00"))
        self.assertEqual(self.api.get("/api/dashboard/").data["kpis"]["chiffre_affaires"], 60800)

        second = self.sell(300)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual([(row["collecte"], row["quantite"])
                          for row in second.data["origines_stock"]], [(b, 212), (c, 88)])
        state = self.stock()
        self.assertEqual(state["stock_global"], 1312)
        self.assertEqual({row["id"]: row["restant"] for row in state["collectes"]},
                         {a: 0, b: 0, c: 1312})
        before = (Vente.objects.count(), MouvementOeufs.objects.count(),
                  AffectationMouvementOeufs.objects.count())
        rejected = self.sell(1400)
        self.assertEqual(rejected.status_code, 400, rejected.data)
        self.assertEqual((Vente.objects.count(), MouvementOeufs.objects.count(),
                          AffectationMouvementOeufs.objects.count()), before)
        self.assertEqual(self.stock()["stock_global"], 1312)

    def test_partially_used_and_exhausted_collections_are_skipped_correctly(self):
        a = self.collect(500, days_ago=3)
        b = self.collect(500, days_ago=2)
        loss = MouvementOeufs.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            type_mouvement="CASSE", quantite=300,
        )
        affect_egg_exit(loss, [{"collecte": a, "quantite": 300}])
        first = self.sell(350)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual([(row["collecte"], row["quantite"])
                          for row in first.data["origines_stock"]], [(a, 200), (b, 150)])
        second = self.sell(100)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual([(row["collecte"], row["quantite"])
                          for row in second.data["origines_stock"]], [(b, 100)])
        self.assertEqual({row["id"]: row["restant"] for row in self.stock()["collectes"]},
                         {a: 0, b: 250})

    def test_same_datetime_uses_collection_id_as_stable_tiebreaker(self):
        a = self.collect(100, days_ago=1)
        b = self.collect(100, days_ago=1)
        sale = self.sell(150)
        self.assertEqual(sale.status_code, 201, sale.data)
        self.assertEqual([(row["collecte"], row["quantite"])
                          for row in sale.data["origines_stock"]], [(a, 100), (b, 50)])

    def test_retroactive_sale_excludes_future_collections_and_can_be_insufficient(self):
        a = self.collect(100, days_ago=4)
        b = self.collect(200, days_ago=3)
        c = self.collect(500, days_ago=1)
        denied = self.sell(400, days_ago=2)
        self.assertEqual(denied.status_code, 400, denied.data)
        self.assertIn("300 œufs disponibles", denied.data["error"])
        self.assertEqual(Vente.objects.count(), 0)
        accepted = self.sell(250, days_ago=2)
        self.assertEqual(accepted.status_code, 201, accepted.data)
        self.assertEqual([(row["collecte"], row["quantite"])
                          for row in accepted.data["origines_stock"]], [(a, 100), (b, 150)])
        self.assertEqual(self.stock()["stock_global"], 550)
        self.assertEqual(self.stock()["collectes"][0]["id"], c)
        self.assertEqual(self.stock()["collectes"][0]["restant"], 500)

    def test_future_collection_cannot_be_sold_today(self):
        self.collect(100, days_ago=-1)
        response = self.sell(20)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Vente.objects.count(), 0)
        self.assertEqual(self.stock()["stock_global"], 100)

    def test_legacy_unallocated_exit_blocks_fifo_without_inventing_origin(self):
        a = self.collect(500, days_ago=3)
        b = self.collect(700, days_ago=2)
        old_exit = MouvementOeufs.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            type_mouvement="VENTE", quantite=600,
        )
        self.assertEqual(self.stock()["stock_global"], 600)
        self.assertFalse(self.stock()["origines_completes"])
        before = (Vente.objects.count(), MouvementOeufs.objects.count())
        response = self.sell(500)
        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("Origine du stock", response.data["error"])
        self.assertEqual((Vente.objects.count(), MouvementOeufs.objects.count()), before)
        self.assertEqual(old_exit.affectations.count(), 0)
        self.assertEqual({row["id"]: row["restant"] for row in self.stock()["collectes"]},
                         {a: None, b: None})

    def test_unattributed_entry_or_inconsistent_production_also_blocks_fifo(self):
        source = self.collect(100)
        extra = MouvementOeufs.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            type_mouvement="PRODUCTION", quantite=50,
        )
        self.assertEqual(self.sell(10).status_code, 400)
        extra.delete()
        movement = MouvementOeufs.objects.get(collecte_id=source)
        movement.quantite = 90
        movement.save()
        self.assertEqual(self.sell(10).status_code, 400)
        self.assertEqual(Vente.objects.count(), 0)

    def test_lot_and_tenant_stock_never_compensate_for_another_lot(self):
        self.collect(100)
        species = Espece.objects.create(nom="Autre", exploitation=self.user.exploitation)
        other_lot = Lot.objects.create(
            nom="Autres pondeuses", espece=species, exploitation=self.user.exploitation,
            date_debut=timezone.localdate(), type_production="OEUFS",
        )
        self.collect(1000, lot=other_lot)
        foreign_user = User.objects.create_user(username="autre-fifo")
        foreign_species = Espece.objects.create(
            nom="Autre", exploitation=foreign_user.exploitation,
        )
        foreign_lot = Lot.objects.create(
            nom="Autre ferme", espece=foreign_species,
            exploitation=foreign_user.exploitation, date_debut=timezone.localdate(),
            type_production="OEUFS",
        )
        foreign_collection = CollecteOeufs.objects.create(
            lot=foreign_lot, exploitation=foreign_user.exploitation,
            collecte_at=self.at(1), nombre_collecte=5000,
        )
        MouvementOeufs.objects.create(
            lot=foreign_lot, exploitation=foreign_user.exploitation,
            collecte=foreign_collection, type_mouvement="PRODUCTION", quantite=5000,
        )
        self.assertEqual(self.sell(200).status_code, 400)
        self.assertEqual(self.sell(200, lot=foreign_lot).status_code, 400)
        self.assertEqual(self.stock()["stock_global"], 100)
        self.assertEqual(self.stock(other_lot)["stock_global"], 1000)
        self.assertEqual(AffectationMouvementOeufs.objects.count(), 0)

    def test_allocation_error_rolls_back_financial_sale_and_stock(self):
        self.collect(100, days_ago=3)
        self.collect(100, days_ago=2)
        with patch("core.egg_services.AffectationMouvementOeufs.objects.bulk_create",
                   side_effect=ValueError("allocation failure")):
            response = self.sell(150)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Vente.objects.count(), 0)
        self.assertEqual(VenteOeufs.objects.count(), 0)
        self.assertEqual(MouvementOeufs.objects.filter(type_mouvement="VENTE").count(), 0)
        self.assertEqual(AffectationMouvementOeufs.objects.count(), 0)
        self.assertEqual(self.stock()["stock_global"], 200)

    def test_unpaid_deletion_releases_fifo_and_paid_deletion_is_refused(self):
        a = self.collect(100, days_ago=3)
        b = self.collect(200, days_ago=2)
        unpaid = self.sell(150)
        self.assertEqual(unpaid.status_code, 201, unpaid.data)
        self.assertEqual(self.api.delete(f"/api/oeufs/ventes/{unpaid.data['id']}/").status_code, 204)
        self.assertEqual(self.stock()["stock_global"], 300)
        self.assertEqual(AffectationMouvementOeufs.objects.count(), 0)
        paid = self.sell(150)
        payment = self.api.post("/api/payments/create/", {
            "client": self.client.pk, "vente": paid.data["vente"], "montant": 5000,
        }, format="json")
        self.assertEqual(payment.status_code, 201, payment.data)
        self.assertEqual(self.api.delete(f"/api/oeufs/ventes/{paid.data['id']}/").status_code, 400)
        self.assertEqual({row["id"]: row["restant"] for row in self.stock()["collectes"]},
                         {a: 0, b: 150})
        self.assertEqual(Vente.objects.count(), 1)
