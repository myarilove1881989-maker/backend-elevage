from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
from unittest.mock import patch

from django.db.models.deletion import ProtectedError
from django.test import TestCase
from rest_framework.test import APIClient

from core.egg_services import get_egg_stock, get_live_birds
from core.models import (
    Achat, AffectationMouvementOeufs, Client, Depense, Espece, Lettrage,
    Lot, Mouvement, MouvementOeufs, Payment, User, Vente,
)
from core.views import get_lot_stock


class BirthMovementApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='birth-owner')
        self.other = User.objects.create_user(username='birth-other')
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.species = Espece.objects.create(nom='Porc', exploitation=self.user.exploitation)
        self.parent = Lot.objects.create(
            nom='Truies', espece=self.species, exploitation=self.user.exploitation,
            date_debut=date(2026, 9, 1), type_production='REPRODUCTION',
        )
        self.client = Client.objects.create(nom='Client test', exploitation=self.user.exploitation)

    def birth(self, total=12, **changes):
        payload = {
            'lot': self.parent.pk, 'type_mouvement': 'NAISSANCE',
            'date': '2026-10-01', 'total_naissances': total,
            'mort_nes': 2, 'note': 'Portée normale',
            'nom_nouveau_lot': 'Porcelets octobre',
        }
        payload.update(changes)
        return self.api.post('/api/mouvements/create/', payload, format='json')

    def exit(self, lot, kind, count):
        payload = {
            'lot': lot.pk, 'type_mouvement': kind,
            'quantite': count, 'date': '2026-10-03',
        }
        if kind == 'VENTE':
            payload.update(client=self.client.pk, prix_unitaire='5000.00')
        return self.api.post('/api/mouvements/create/', payload, format='json')

    def test_birth_creates_child_lot_with_only_survivors_and_parent_unchanged(self):
        response = self.birth()
        self.assertEqual(response.status_code, 201, response.data)
        child = Lot.objects.get(pk=response.data['nouveau_lot']['id'])
        movement = Mouvement.objects.get(pk=response.data['id'])
        self.assertEqual((response.data['total_naissances'], response.data['nes_vivants']), (12, 10))
        self.assertEqual((movement.quantite, movement.quantite_signee, movement.mort_nes), (10, 10, 2))
        self.assertEqual((movement.note, movement.date), ('Portée normale', date(2026, 10, 1)))
        self.assertEqual((movement.lot_origine, movement.lot), (self.parent, child))
        self.assertEqual((child.nom, child.espece, child.exploitation),
                         ('Porcelets octobre', self.species, self.user.exploitation))
        self.assertEqual((child.type_production, child.statut_production), ('CHAIR', 'ELEVAGE'))
        self.assertEqual((child.date_debut, child.date_naissance),
                         (date(2026, 10, 1), date(2026, 10, 1)))
        self.assertEqual((self.parent.stock, get_lot_stock(self.parent)), (0, 0))
        self.assertEqual((child.stock, get_lot_stock(child)), (10, 10))
        self.assertEqual((Achat.objects.count(), Vente.objects.count(),
                          Depense.objects.count(), Payment.objects.count(),
                          Lettrage.objects.count()), (0, 0, 0, 0, 0))
        dashboard = self.api.get('/api/dashboard/').data['kpis']
        self.assertEqual((dashboard['stock'], dashboard['chiffre_affaires']), (10, 0))
        detail = self.api.get(f'/api/stock-detail/?lot={child.pk}').data
        self.assertEqual((detail['stock_initial'], detail['naissances'], detail['stock_restant']), (10, 10, 10))
        parent_detail = self.api.get(f'/api/lots/{self.parent.pk}/').data
        child_detail = self.api.get(f'/api/lots/{child.pk}/').data
        self.assertEqual(parent_detail['mouvements'], [])
        self.assertEqual(parent_detail['naissances_issues'][0]['nouveau_lot_id'], child.pk)
        self.assertEqual(parent_detail['naissances_issues'][0]['total_naissances'], 12)
        self.assertEqual(child_detail['mouvements'][0]['mort_nes'], 2)
        self.assertEqual(child_detail['mouvements'][0]['lot_origine'], self.parent.pk)

    def test_sale_mortality_donation_and_theft_reduce_new_lot_only(self):
        purchase = Achat.objects.create(
            lot=self.parent, exploitation=self.user.exploitation, date=date(2026, 9, 1),
            quantite=20, prix_total=Decimal('400'), prix_unitaire=Decimal('20'),
        )
        Mouvement.objects.create(
            lot=self.parent, exploitation=self.user.exploitation,
            type_mouvement='ACHAT', quantite=purchase.quantite,
        )
        response = self.birth()
        child = Lot.objects.get(pk=response.data['nouveau_lot']['id'])
        self.assertEqual((get_lot_stock(self.parent), get_lot_stock(child)), (20, 10))
        self.assertEqual(self.api.get('/api/dashboard/').data['kpis']['stock'], 30)
        for kind, count in [('VENTE', 3), ('MORTALITE', 2), ('DON', 1), ('VOL', 1)]:
            result = self.exit(child, kind, count)
            self.assertEqual(result.status_code, 201, result.data)
        self.assertEqual((get_lot_stock(self.parent), get_lot_stock(child)), (20, 3))
        self.assertEqual(Vente.objects.get().lot, child)
        self.assertEqual(self.api.get('/api/dashboard/').data['kpis']['stock'], 23)
        self.assertEqual(self.exit(child, 'VENTE', 4).status_code, 400)

    def test_two_births_create_distinct_lots_and_valid_type(self):
        first = self.birth()
        second = self.birth(8, date='2026-10-04', mort_nes=0,
                            nom_nouveau_lot='Pondeuses octobre', type_production='OEUFS')
        self.assertEqual((first.status_code, second.status_code), (201, 201))
        self.assertNotEqual(first.data['nouveau_lot']['id'], second.data['nouveau_lot']['id'])
        self.assertEqual(Lot.objects.get(pk=second.data['nouveau_lot']['id']).type_production, 'OEUFS')
        self.assertEqual(self.parent.stock, 0)
        self.assertEqual(len(self.api.get(f'/api/lots/{self.parent.pk}/').data['naissances_issues']), 2)

    def test_global_stock_increases_only_by_ten_from_one_hundred(self):
        Achat.objects.create(
            lot=self.parent, exploitation=self.user.exploitation,
            date=date(2026, 9, 1), quantite=100,
            prix_total=Decimal('1000'), prix_unitaire=Decimal('10'),
        )
        Mouvement.objects.create(
            lot=self.parent, type_mouvement='ACHAT', quantite=100,
            exploitation=self.user.exploitation,
        )
        self.assertEqual(self.api.get('/api/dashboard/').data['kpis']['stock'], 100)
        self.assertEqual(self.birth().status_code, 201)
        self.assertEqual(self.api.get('/api/dashboard/').data['kpis']['stock'], 110)
        self.assertEqual(get_lot_stock(self.parent), 100)

    def test_invalid_births_create_nothing(self):
        for total in (0, -1, 1.5, '1.5', True, None):
            self.assertEqual(self.birth(total).status_code, 400)
        for changes in (
            {'mort_nes': -1}, {'mort_nes': 1.5}, {'mort_nes': 12},
            {'mort_nes': 14}, {'mort_nes': True}, {'nom_nouveau_lot': ''},
            {'nom_nouveau_lot': '  '}, {'nom_nouveau_lot': 'x' * 101},
            {'type_production': 'UNKNOWN'}, {'date': 'yesterday'},
            {'client': self.client.pk}, {'prix_unitaire': '10.00'},
            {'quantite': 12}, {'espece': self.species.pk},
            {'exploitation': self.user.exploitation_id},
        ):
            response = self.birth(**changes)
            self.assertEqual(response.status_code, 400, (changes, response.data))
        self.assertEqual((Lot.objects.count(), Mouvement.objects.count()), (1, 0))

    def test_atomic_rollback_if_movement_fails(self):
        with patch('core.views.Mouvement.objects.create', side_effect=RuntimeError('DB error')):
            with self.assertRaises(RuntimeError):
                self.birth()
        self.assertEqual((Lot.objects.count(), Mouvement.objects.count()), (1, 0))

    def test_tenant_cannot_create_see_or_delete_foreign_lot(self):
        own = self.birth()
        foreign_species = Espece.objects.create(nom='Porc', exploitation=self.other.exploitation)
        foreign_parent = Lot.objects.create(
            nom='Autre', espece=foreign_species,
            exploitation=self.other.exploitation, date_debut=date(2026, 9, 1),
        )
        self.assertIn(self.birth(lot=foreign_parent.pk).status_code, (403, 404))
        self.assertEqual(Lot.objects.count(), 3)
        self.assertEqual(self.api.get(f'/api/lots/{foreign_parent.pk}/').status_code, 403)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.delete(f"/api/mouvements/delete/{own.data['id']}/").status_code, 403)
        self.assertEqual(self.api.get(f'/api/lots/{self.parent.pk}/').status_code, 403)
        self.assertEqual(self.api.get('/api/mouvements/').data, [])

    def test_delete_unused_birth_removes_child_but_keeps_parent(self):
        response = self.birth()
        child_id = response.data['nouveau_lot']['id']
        removed = self.api.delete(f"/api/mouvements/delete/{response.data['id']}/")
        self.assertEqual(removed.status_code, 200, removed.data)
        self.assertFalse(Lot.objects.filter(pk=child_id).exists())
        self.assertTrue(Lot.objects.filter(pk=self.parent.pk).exists())
        self.assertEqual(Mouvement.objects.count(), 0)

    def test_delete_birth_with_sale_is_blocked(self):
        response = self.birth()
        child = Lot.objects.get(pk=response.data['nouveau_lot']['id'])
        self.assertEqual(self.exit(child, 'VENTE', 8).status_code, 201)
        removed = self.api.delete(f"/api/mouvements/delete/{response.data['id']}/")
        self.assertEqual(removed.status_code, 409, removed.data)
        self.assertEqual(child.stock, 2)
        self.assertEqual(Vente.objects.count(), 1)
        self.assertTrue(Mouvement.objects.filter(pk=response.data['id']).exists())

    def test_parent_protected_and_legacy_birth_has_no_fabricated_origin(self):
        response = self.birth()
        with self.assertRaises(ProtectedError):
            self.parent.delete()
        self.assertTrue(Lot.objects.filter(pk=response.data['nouveau_lot']['id']).exists())
        legacy = Mouvement.objects.create(
            lot=self.parent, type_mouvement='NAISSANCE', quantite=5,
            mort_nes=1, exploitation=self.user.exploitation,
        )
        self.assertIsNone(legacy.lot_origine_id)
        self.assertEqual(len(self.api.get(f'/api/lots/{self.parent.pk}/').data['naissances_issues']), 1)
        self.assertEqual(self.api.put(f'/api/mouvements/delete/{legacy.pk}/', {}).status_code, 405)

    def test_deleting_parent_purchase_cannot_erase_child_lineage(self):
        purchase = Achat.objects.create(
            lot=self.parent, exploitation=self.user.exploitation,
            date=date(2026, 9, 1), quantite=20,
            prix_total=Decimal('400'), prix_unitaire=Decimal('20'),
        )
        birth = self.birth()
        result = self.api.delete(f'/api/achats/delete/{purchase.pk}/')
        self.assertEqual(result.status_code, 409, result.data)
        self.assertTrue(Achat.objects.filter(pk=purchase.pk).exists())
        self.assertTrue(Lot.objects.filter(pk=birth.data['nouveau_lot']['id']).exists())

    def test_growth_and_egg_stock_use_the_new_lot(self):
        birth = self.birth()
        child = Lot.objects.get(pk=birth.data['nouveau_lot']['id'])
        weighed = self.api.post('/api/production/pesees/', {
            'lot': child.pk,
            'pesee_at': datetime(2026, 10, 2, 8, tzinfo=dt_timezone.utc).isoformat(),
            'nombre_animaux_peses': 10, 'poids_total_kg': '20.000',
        }, format='json')
        self.assertEqual(weighed.status_code, 201, weighed.data)
        after = self.api.get(f'/api/production/pesees/?lot={child.pk}')
        self.assertEqual(after.data['effectif_actuel'], 10)
        self.assertEqual(after.data['biomasse_estimee_kg'], Decimal('20.000'))
        self.assertEqual((get_live_birds(child), get_live_birds(self.parent)), (10, 0))
        self.assertEqual(MouvementOeufs.objects.count(), 0)
        self.assertEqual(AffectationMouvementOeufs.objects.count(), 0)
        self.assertEqual(get_egg_stock(self.user.exploitation), 0)
