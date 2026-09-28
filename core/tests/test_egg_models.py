from datetime import date
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.utils import timezone

from core.models import (
    CollecteOeufs,
    ConsommationAliment,
    Espece,
    Lot,
    MouvementOeufs,
    User,
)


class EggModelTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="ferme-pondeuse", password="test-pass")
        self.espece = Espece.objects.create(
            nom="Poulet",
            exploitation=self.user.exploitation,
        )
        self.lot_ponte = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Pondeuses A",
            date_debut=date(2026, 1, 1),
            type_production="OEUFS",
            statut_production="PONTE",
        )

    def test_existing_lot_defaults_remain_meat_production(self):
        lot = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Poulets de chair",
            date_debut=date(2026, 1, 1),
        )

        self.assertEqual(lot.type_production, "CHAIR")
        self.assertEqual(lot.statut_production, "ELEVAGE")

    def test_collection_computes_markettable_eggs(self):
        collecte = CollecteOeufs(
            exploitation=self.user.exploitation,
            lot=self.lot_ponte,
            collecte_at=timezone.now(),
            nombre_collecte=100,
            nombre_casses=4,
            nombre_declasses=6,
            nombre_consommes_donnes=5,
        )

        collecte.full_clean()
        self.assertEqual(collecte.nombre_commercialisable, 85)

    def test_collection_rejects_invalid_breakdown(self):
        collecte = CollecteOeufs(
            exploitation=self.user.exploitation,
            lot=self.lot_ponte,
            nombre_collecte=10,
            nombre_casses=5,
            nombre_declasses=4,
            nombre_consommes_donnes=2,
        )

        with self.assertRaises(ValidationError):
            collecte.full_clean()

    def test_collection_rejects_non_laying_lot(self):
        lot_chair = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Chair B",
            date_debut=date(2026, 1, 1),
        )
        collecte = CollecteOeufs(
            exploitation=self.user.exploitation,
            lot=lot_chair,
            nombre_collecte=10,
        )

        with self.assertRaises(ValidationError):
            collecte.full_clean()

    def test_collection_rejects_another_tenant_lot(self):
        other_user = User.objects.create_user(username="autre-ferme", password="test-pass")
        collecte = CollecteOeufs(
            exploitation=other_user.exploitation,
            lot=self.lot_ponte,
            nombre_collecte=10,
        )

        with self.assertRaises(ValidationError):
            collecte.full_clean()

    def test_egg_movements_keep_a_signed_audit_trail(self):
        production = MouvementOeufs.objects.create(
            exploitation=self.user.exploitation,
            lot=self.lot_ponte,
            type_mouvement="PRODUCTION",
            quantite=90,
        )
        sale = MouvementOeufs.objects.create(
            exploitation=self.user.exploitation,
            lot=self.lot_ponte,
            type_mouvement="VENTE",
            quantite=30,
        )
        adjustment = MouvementOeufs.objects.create(
            exploitation=self.user.exploitation,
            lot=self.lot_ponte,
            type_mouvement="AJUSTEMENT",
            quantite=-2,
        )

        self.assertEqual(production.quantite_signee, 90)
        self.assertEqual(sale.quantite_signee, -30)
        self.assertEqual(adjustment.quantite_signee, -2)
        stock = sum(
            self.lot_ponte.mouvements_oeufs.values_list("quantite_signee", flat=True)
        )
        self.assertEqual(stock, 58)

    def test_feed_cost_uses_linked_expense_or_unit_price(self):
        consommation = ConsommationAliment(
            exploitation=self.user.exploitation,
            lot=self.lot_ponte,
            date=date(2026, 9, 21),
            quantite_kg=Decimal("12.500"),
            prix_kg=Decimal("350.00"),
        )

        consommation.full_clean()
        self.assertEqual(consommation.cout_calcule, Decimal("4375.00000"))
