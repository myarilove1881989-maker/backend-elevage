from datetime import date, datetime
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.tests.functional_fixtures import operator_for_tests

from core.models import Client, CollecteOeufs, Espece, Lot, MouvementOeufs, User, Vente, VenteOeufs


class EggSalesApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="vente-oeufs-owner", password="test-pass")
        self.user = operator_for_tests(self.owner, "vente-oeufs", [
            "egg_sales.create", "egg_sales.delete", "cash.create",
        ])
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.espece = Espece.objects.create(
            nom="Poulet",
            exploitation=self.user.exploitation,
        )
        self.lot = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Pondeuses ventes",
            date_debut=date(2026, 1, 1),
            type_production="OEUFS",
            statut_production="PONTE",
        )
        self.client = Client.objects.create(
            nom="Client œufs",
            exploitation=self.user.exploitation,
        )
        collected_at = timezone.make_aware(datetime(2026, 9, 20, 8))
        collection = CollecteOeufs.objects.create(
            exploitation=self.user.exploitation, lot=self.lot,
            collecte_at=collected_at, nombre_collecte=300,
        )
        MouvementOeufs.objects.create(
            exploitation=self.user.exploitation,
            lot=self.lot,
            type_mouvement="PRODUCTION",
            quantite=300,
            collecte=collection,
            date=collected_at,
            created_by=self.user,
        )

    def sale_payload(self, **overrides):
        payload = {
            "lot": self.lot.id,
            "client": self.client.id,
            "date": "2026-09-21",
            "conditionnement": "PLATEAU",
            "nombre_conditionnements": 2,
            "oeufs_par_conditionnement": 999,
            "prix_unitaire_conditionnement": "4500.00",
        }
        payload.update(overrides)
        return payload

    def test_plateau_conversion_is_enforced_by_backend(self):
        response = self.api.post(
            "/api/oeufs/ventes/",
            self.sale_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["oeufs_par_conditionnement"], 30)
        self.assertEqual(response.data["nombre_oeufs"], 60)
        self.assertEqual(Decimal(response.data["montant_total"]), Decimal("9000.00"))
        vente = Vente.objects.get(pk=response.data["vente"])
        self.assertEqual(vente.quantite, 2)
        self.assertEqual(vente.montant_total, Decimal("9000.00"))
        self.assertEqual(
            MouvementOeufs.objects.get(vente_oeufs_id=response.data["id"]).quantite_signee,
            -60,
        )

    def test_carton_requires_a_positive_configurable_capacity(self):
        response = self.api.post(
            "/api/oeufs/ventes/",
            self.sale_payload(
                conditionnement="CARTON",
                oeufs_par_conditionnement=0,
            ),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("oeufs_par_conditionnement", response.data)

    def test_sale_cannot_exceed_available_stock(self):
        response = self.api.post(
            "/api/oeufs/ventes/",
            self.sale_payload(nombre_conditionnements=11),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("Stock d'œufs insuffisant", response.data["error"])
        self.assertEqual(VenteOeufs.objects.count(), 0)

    def test_sale_rejects_client_from_another_tenant(self):
        other_user = User.objects.create_user(username="client-autre", password="test-pass")
        other_client = Client.objects.create(
            nom="Client autre ferme",
            exploitation=other_user.exploitation,
        )

        response = self.api.post(
            "/api/oeufs/ventes/",
            self.sale_payload(client=other_client.id),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("client", response.data)

    def test_egg_sale_uses_existing_debt_and_payment_ledger(self):
        sale_response = self.api.post(
            "/api/oeufs/ventes/",
            self.sale_payload(),
            format="json",
        )
        vente_id = sale_response.data["vente"]

        debt_response = self.api.get("/api/dettes-clients/")
        debt = next(item for item in debt_response.data if item["id"] == self.client.id)
        self.assertEqual(debt["total_facture"], 9000.0)
        self.assertEqual(debt["reste"], 9000.0)

        payment_response = self.api.post(
            "/api/payments/create/",
            {"client": self.client.id, "vente": vente_id, "montant": 4000},
            format="json",
        )
        self.assertEqual(payment_response.status_code, 201)

        detail_response = self.api.get(f"/api/oeufs/ventes/{sale_response.data['id']}/")
        self.assertEqual(detail_response.data["montant_paye"], 4000.0)
        self.assertEqual(detail_response.data["reste"], 5000.0)
        self.assertEqual(detail_response.data["statut"], "PARTIEL")

    def test_paid_sale_cannot_be_deleted(self):
        sale_response = self.api.post(
            "/api/oeufs/ventes/",
            self.sale_payload(),
            format="json",
        )
        self.api.post(
            "/api/payments/create/",
            {"client": self.client.id, "vente": sale_response.data["vente"], "montant": 1000},
            format="json",
        )

        response = self.api.delete(f"/api/oeufs/ventes/{sale_response.data['id']}/")

        self.assertEqual(response.status_code, 400)
        self.assertTrue(VenteOeufs.objects.filter(id=sale_response.data["id"]).exists())

    def test_unpaid_sale_deletion_is_refused_and_preserves_stock_and_debt(self):
        from core.models import AffectationMouvementOeufs

        sale_response = self.api.post(
            "/api/oeufs/ventes/",
            self.sale_payload(),
            format="json",
        )
        self.assertEqual(sale_response.status_code, 201, sale_response.data)

        def ledger():
            return {
                model._meta.label: list(model.objects.order_by("pk").values())
                for model in (CollecteOeufs, Vente, VenteOeufs, MouvementOeufs,
                              AffectationMouvementOeufs)
            }

        before = ledger()
        response = self.api.delete(f"/api/oeufs/ventes/{sale_response.data['id']}/")

        self.assertEqual(response.status_code, 400, response.data)
        self.assertIn("HISTORY_IMMUTABLE_USE_SUPPORTED_CANCELLATION", str(response.data))
        self.assertEqual(ledger(), before)
        self.assertTrue(Vente.objects.filter(id=sale_response.data["vente"]).exists())
        stock = sum(MouvementOeufs.objects.values_list("quantite_signee", flat=True))
        self.assertEqual(stock, 240)
        detail = self.api.get(f"/api/oeufs/ventes/{sale_response.data['id']}/")
        self.assertEqual(detail.status_code, 200, detail.data)
        self.assertEqual(detail.data["montant_paye"], 0.0)
        self.assertEqual(detail.data["reste"], 9000.0)
