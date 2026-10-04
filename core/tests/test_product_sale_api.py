from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.egg_services import get_dated_egg_stock, get_egg_stock
from core.models import (
    Achat, Client, CollecteOeufs, Espece, Lot, Mouvement,
    MouvementOeufs, User, Vente, VenteOeufs,
)


class ProductSaleApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="phase8")
        self.other = User.objects.create_user(username="phase8-other")
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        species = Espece.objects.create(nom="Poulet", exploitation=self.user.exploitation)
        self.chair = Lot.objects.create(
            nom="Chair A", espece=species, exploitation=self.user.exploitation,
            date_debut=date(2026, 1, 1), type_production="CHAIR",
        )
        self.eggs = Lot.objects.create(
            nom="Pondeuses A", espece=species, exploitation=self.user.exploitation,
            date_debut=date(2026, 1, 1), type_production="OEUFS",
        )
        self.client = Client.objects.create(
            nom="Restaurant", exploitation=self.user.exploitation,
        )
        Achat.objects.create(
            lot=self.chair, exploitation=self.user.exploitation,
            date=date(2026, 9, 1), quantite=100,
            prix_total=Decimal("10000"), prix_unitaire=Decimal("100"),
        )
        Mouvement.objects.create(
            lot=self.chair, exploitation=self.user.exploitation,
            type_mouvement="ACHAT", quantite=100,
        )
        self.collection = CollecteOeufs.objects.create(
            lot=self.eggs, exploitation=self.user.exploitation,
            collecte_at=timezone.now() - timedelta(days=1), nombre_collecte=1000,
        )
        MouvementOeufs.objects.create(
            lot=self.eggs, exploitation=self.user.exploitation,
            collecte=self.collection, type_mouvement="PRODUCTION", quantite=1000,
        )

    def animal_payload(self, **overrides):
        payload = {
            "lot": self.chair.pk, "client": self.client.pk,
            "type_mouvement": "VENTE", "quantite": 10,
            "prix_unitaire": "5000.00", "date": "2026-09-28",
        }
        payload.update(overrides)
        return payload

    def egg_payload(self, **overrides):
        payload = {
            "lot": self.eggs.pk, "client": self.client.pk,
            "conditionnement": "COMPOSE", "nombre_alveoles": 20,
            "oeufs_supplementaires": 8, "prix_total": "60800.00",
            "date": timezone.localdate().isoformat(),
        }
        payload.update(overrides)
        return payload

    def animal_sale(self, **overrides):
        response = self.api.post(
            "/api/mouvements/create/", self.animal_payload(**overrides), format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return Mouvement.objects.get(pk=response.data["id"])

    def egg_sale(self, **overrides):
        response = self.api.post(
            "/api/oeufs/ventes/", self.egg_payload(**overrides), format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        return VenteOeufs.objects.get(pk=response.data["id"])

    def test_animal_sale_keeps_stock_client_ca_and_partial_payment(self):
        movement = self.animal_sale()
        sale = movement.vente
        self.assertEqual(sale.quantite, 10)
        self.assertEqual(sale.montant_total, Decimal("50000.00"))
        self.assertEqual(self.chair.stock, 90)
        debt = self.api.get("/api/dettes-clients/").data[0]
        self.assertEqual(debt["reste"], 50000)
        self.assertEqual(self.api.get("/api/dashboard/").data["kpis"]["chiffre_affaires"], 50000)
        history = self.api.get(f"/api/clients/{self.client.pk}/ventes/").data
        self.assertEqual(history[0]["produit_vendu"], "ANIMAUX")
        paid = self.api.post("/api/payments/create/", {
            "client": self.client.pk, "vente": sale.pk, "montant": 20000,
        }, format="json")
        self.assertEqual(paid.status_code, 201, paid.data)
        self.assertEqual(self.api.get(f"/api/clients/{self.client.pk}/ventes/").data[0]["reste"], 30000)

    def test_invalid_animal_sale_does_not_leave_a_movement(self):
        for change in ({"client": 99999}, {"prix_unitaire": "0"}, {"quantite": -1}):
            response = self.api.post("/api/mouvements/create/", self.animal_payload(**change), format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Mouvement.objects.filter(type_mouvement="VENTE").count(), 0)
        self.assertEqual(Vente.objects.count(), 0)
        with patch("core.views.Vente.objects.create", side_effect=ValueError("DB error")):
            with self.assertRaises(ValueError):
                self.animal_sale()
        self.assertEqual(Mouvement.objects.filter(type_mouvement="VENTE").count(), 0)

    def test_other_animal_movements_still_decrease_animal_stock(self):
        for movement_type in ("MORTALITE", "DON", "VOL"):
            response = self.api.post("/api/mouvements/create/", {
                "lot": self.chair.pk, "type_mouvement": movement_type,
                "quantite": 2, "date": "2026-09-28",
            }, format="json")
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.chair.stock, 94)
        self.assertEqual(Vente.objects.count(), 0)

    def test_animal_sale_rejects_another_tenants_lot_and_customer(self):
        other_species = Espece.objects.create(
            nom="Porc", exploitation=self.other.exploitation,
        )
        other_lot = Lot.objects.create(
            nom="Other", espece=other_species, exploitation=self.other.exploitation,
            date_debut=date(2026, 1, 1),
        )
        other_client = Client.objects.create(
            nom="Other", exploitation=self.other.exploitation,
        )
        for change in ({"lot": other_lot.pk}, {"client": other_client.pk}):
            response = self.api.post(
                "/api/mouvements/create/", self.animal_payload(**change), format="json",
            )
            self.assertIn(response.status_code, (400, 403), response.data)
        self.assertEqual(Vente.objects.count(), 0)
        self.assertEqual(Mouvement.objects.filter(type_mouvement="VENTE").count(), 0)

    def test_animal_deletion_targets_only_its_exact_sale_and_protects_payments(self):
        first = self.animal_sale()
        second = self.animal_sale()
        first_sale_id, second_sale_id = first.vente_id, second.vente_id
        deleted = self.api.delete(f"/api/mouvements/delete/{first.pk}/")
        self.assertEqual(deleted.status_code, 200, deleted.data)
        self.assertFalse(Vente.objects.filter(pk=first_sale_id).exists())
        self.assertTrue(Vente.objects.filter(pk=second_sale_id).exists())
        self.assertEqual(self.chair.stock, 90)
        self.api.post("/api/payments/create/", {
            "client": self.client.pk, "vente": second_sale_id, "montant": 1000,
        }, format="json")
        self.assertEqual(self.api.delete(f"/api/mouvements/delete/{second.pk}/").status_code, 400)
        self.assertTrue(Vente.objects.filter(pk=second_sale_id).exists())

    def test_deleting_a_purchase_cannot_cascade_into_sales_and_payments(self):
        movement = self.animal_sale()
        sale = movement.vente
        paid = self.api.post("/api/payments/create/", {
            "client": self.client.pk, "vente": sale.pk, "montant": 1000,
        }, format="json")
        self.assertEqual(paid.status_code, 201, paid.data)
        purchase = Achat.objects.get(lot=self.chair)
        response = self.api.delete(f"/api/achats/delete/{purchase.pk}/")
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(Achat.objects.filter(pk=purchase.pk).exists())
        self.assertTrue(Vente.objects.filter(pk=sale.pk).exists())
        self.assertEqual(sale.lettrages.count(), 1)

    def test_payment_and_lettrage_are_created_together(self):
        sale = self.animal_sale().vente
        with patch("core.views.Lettrage.objects.create", side_effect=ValueError("DB error")):
            with self.assertRaises(ValueError):
                self.api.post("/api/payments/create/", {
                    "client": self.client.pk, "vente": sale.pk, "montant": 1000,
                }, format="json")
        self.assertEqual(self.client.payments.count(), 0)
        self.assertEqual(sale.lettrages.count(), 0)

    def test_historical_unlinked_sale_is_not_guessed_from_quantity_and_date(self):
        old = Mouvement.objects.create(
            lot=self.chair, exploitation=self.user.exploitation,
            client=self.client, type_mouvement="VENTE", quantite=10,
            prix_unitaire=Decimal("5000"), date=date(2026, 9, 28),
        )
        historic_sale = Vente.objects.create(
            lot=self.chair, client=self.client, date=old.date,
            quantite=10, prix_unitaire=Decimal("5000"),
        )
        self.assertEqual(self.api.delete(f"/api/mouvements/delete/{old.pk}/").status_code, 409)
        self.assertTrue(Vente.objects.filter(pk=historic_sale.pk).exists())
        self.assertTrue(Mouvement.objects.filter(pk=old.pk).exists())
        self.assertEqual(self.api.get(f"/api/clients/{self.client.pk}/ventes/").data[0]["produit_vendu"], "ANIMAUX")

    def test_mixed_egg_sale_reuses_vente_and_allocates_stock_fifo(self):
        detail = self.egg_sale()
        self.assertEqual(detail.nombre_oeufs, 608)
        self.assertEqual(detail.vente.quantite, 1)
        self.assertEqual(detail.vente.montant_total, Decimal("60800.00"))
        self.assertEqual(detail.montant_total, detail.vente.montant_total)
        self.assertEqual(get_egg_stock(self.user.exploitation, self.eggs), 392)
        self.assertEqual(self.eggs.stock, 0)  # Les poules n'ont pas été vendues.
        self.assertEqual(self.api.get("/api/dashboard/").data["kpis"]["chiffre_affaires"], 60800)
        self.assertEqual(self.api.get("/api/dettes-clients/").data[0]["reste"], 60800)
        history = self.api.get(f"/api/clients/{self.client.pk}/ventes/").data[0]
        self.assertEqual(history["produit_vendu"], "OEUFS")
        self.assertEqual(history["nombre_oeufs"], 608)
        self.assertEqual(history["conditionnement"], "COMPOSE")
        dated = get_dated_egg_stock(self.user.exploitation, self.eggs)
        self.assertEqual(dated["sorties_non_attribuees"], 0)
        self.assertTrue(dated["origines_completes"])
        self.assertEqual(dated["collectes"][0]["restant"], 392)
        self.assertEqual(detail.mouvement_stock.affectations.get().quantite, 608)

    def test_exact_total_does_not_need_fractions_of_a_price_per_egg(self):
        detail = self.egg_sale(prix_total="60000.00")
        self.assertEqual(detail.nombre_oeufs, 608)
        self.assertEqual(detail.vente.montant_total, Decimal("60000.00"))
        self.assertEqual(detail.prix_unitaire_conditionnement, Decimal("60000.00"))

    def test_paid_egg_sale_cannot_be_deleted_and_keeps_the_stock_exit(self):
        detail = self.egg_sale()
        payment = self.api.post("/api/payments/create/", {
            "client": self.client.pk, "vente": detail.vente_id, "montant": 10000,
        }, format="json")
        self.assertEqual(payment.status_code, 201, payment.data)
        deleted = self.api.delete(f"/api/oeufs/ventes/{detail.pk}/")
        self.assertEqual(deleted.status_code, 400, deleted.data)
        self.assertTrue(VenteOeufs.objects.filter(pk=detail.pk).exists())
        self.assertEqual(get_egg_stock(self.user.exploitation, self.eggs), 392)

    def test_egg_sale_insufficient_stock_and_invalid_quantities_are_atomic(self):
        invalid = [
            self.egg_payload(nombre_alveoles=34),
            self.egg_payload(nombre_alveoles=0, oeufs_supplementaires=0),
            self.egg_payload(nombre_alveoles=-1),
            self.egg_payload(oeufs_supplementaires=30),
            self.egg_payload(prix_total="0.00"),
            self.egg_payload(nombre_conditionnements=1),
        ]
        for payload in invalid:
            response = self.api.post("/api/oeufs/ventes/", payload, format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Vente.objects.count(), 0)
        self.assertEqual(VenteOeufs.objects.count(), 0)
        self.assertEqual(MouvementOeufs.objects.filter(type_mouvement="VENTE").count(), 0)
        self.assertEqual(get_egg_stock(self.user.exploitation, self.eggs), 1000)

    def test_cross_tenant_lot_client_and_collection_are_rejected(self):
        species = Espece.objects.create(nom="Poulet", exploitation=self.other.exploitation)
        other_lot = Lot.objects.create(
            nom="Autre ponte", espece=species, exploitation=self.other.exploitation,
            type_production="OEUFS", date_debut=date(2026, 1, 1),
        )
        other_client = Client.objects.create(nom="Autre", exploitation=self.other.exploitation)
        other_collection = CollecteOeufs.objects.create(
            lot=other_lot, exploitation=self.other.exploitation,
            collecte_at=timezone.now(), nombre_collecte=100,
        )
        for change in (
            {"lot": self.chair.pk}, {"lot": other_lot.pk},
            {"client": other_client.pk},
            {"affectations": [{"collecte": other_collection.pk, "quantite": 608}]},
        ):
            response = self.api.post("/api/oeufs/ventes/", self.egg_payload(**change), format="json")
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(Vente.objects.count(), 0)
        self.assertEqual(get_egg_stock(self.user.exploitation, self.eggs), 1000)

    def test_explicit_allocation_works_and_deletion_restores_stock(self):
        detail = self.egg_sale(affectations=[{"collecte": self.collection.pk, "quantite": 608}])
        stock = get_dated_egg_stock(self.user.exploitation, self.eggs)
        self.assertTrue(stock["origines_completes"])
        self.assertEqual(stock["collectes"][0]["restant"], 392)
        self.assertEqual(self.api.delete(f"/api/oeufs/ventes/{detail.pk}/").status_code, 204)
        self.assertEqual(get_egg_stock(self.user.exploitation, self.eggs), 1000)
        self.assertEqual(Vente.objects.count(), 0)

    def test_payment_and_accounting_include_animal_and_egg_sales_once(self):
        animal = self.animal_sale()
        eggs = self.egg_sale()
        dashboard = self.api.get("/api/dashboard/").data["kpis"]
        self.assertEqual(dashboard["chiffre_affaires"], 110800)
        self.assertEqual(self.api.get("/api/dettes-clients/").data[0]["reste"], 110800)
        for amount, outstanding in ((40000, 20800), (20800, 0)):
            payment = self.api.post("/api/payments/create/", {
                "client": self.client.pk, "vente": eggs.vente_id, "montant": amount,
            }, format="json")
            self.assertEqual(payment.status_code, 201, payment.data)
            self.assertEqual(Vente.objects.get(pk=eggs.vente_id).reste_a_payer, outstanding)
        self.assertEqual(self.api.get(f"/api/clients/{self.client.pk}/ventes/").data[0]["statut"], "PAYE")
        self.assertEqual(self.api.get("/api/dettes-clients/").data[0]["reste"], 50000)
        self.assertEqual(animal.vente.reste_a_payer, 50000)

    def test_animal_performance_quantity_excludes_egg_packages_but_ca_includes_both(self):
        self.animal_sale()
        self.egg_sale()
        row = self.api.get("/api/performance-especes/").data["classement"][0]
        self.assertEqual(row["quantite_vendue"], 10)
        self.assertEqual(row["marge"], 110800)

    def test_egg_sale_failure_rolls_back_financial_record(self):
        with patch("core.egg_services.MouvementOeufs.objects.create", side_effect=ValueError("DB error")):
            response = self.api.post("/api/oeufs/ventes/", self.egg_payload(), format="json")
            self.assertEqual(response.status_code, 400)
        self.assertEqual(Vente.objects.count(), 0)
        self.assertEqual(VenteOeufs.objects.count(), 0)
