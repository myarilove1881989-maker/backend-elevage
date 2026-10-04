from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal

from django.test import TestCase
from rest_framework.test import APIClient

from core.egg_services import get_egg_stock, get_live_birds
from core.models import (
    Achat, Client, Espece, Lot, Mouvement, MouvementOeufs, User, Vente,
)
from core.views import get_lot_stock


class BirthMovementApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='birth-owner')
        self.other = User.objects.create_user(username='birth-other')
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        species = Espece.objects.create(
            nom='Porc', exploitation=self.user.exploitation,
        )
        self.lot = Lot.objects.create(
            nom='Porcelets octobre', espece=species,
            exploitation=self.user.exploitation,
            date_debut=date(2026, 9, 1), type_production='CHAIR',
        )
        self.client = Client.objects.create(
            nom='Client test', exploitation=self.user.exploitation,
        )

    def birth(self, count=12, **changes):
        payload = {
            'lot': self.lot.pk, 'type_mouvement': 'NAISSANCE',
            'date': '2026-10-01', 'quantite': count,
            'mort_nes': 2, 'note': 'Portée normale',
        }
        payload.update(changes)
        return self.api.post('/api/mouvements/create/', payload, format='json')

    def exit(self, kind, count, **changes):
        payload = {
            'lot': self.lot.pk, 'type_mouvement': kind,
            'quantite': count, 'date': '2026-10-03',
        }
        if kind == 'VENTE':
            payload.update(client=self.client.pk, prix_unitaire='5000.00')
        payload.update(changes)
        return self.api.post('/api/mouvements/create/', payload, format='json')

    def test_birth_enters_only_live_animals_without_purchase_or_sale(self):
        response = self.birth()
        self.assertEqual(response.status_code, 201, response.data)
        movement = Mouvement.objects.get(pk=response.data['id'])
        self.assertEqual(movement.quantite_signee, 12)
        self.assertEqual(movement.mort_nes, 2)
        self.assertEqual(movement.note, 'Portée normale')
        self.assertEqual(movement.date, date(2026, 10, 1))
        self.assertEqual(self.lot.stock, 12)
        self.assertEqual(get_lot_stock(self.lot), 12)
        self.assertEqual(Achat.objects.count(), 0)
        self.assertEqual(Vente.objects.count(), 0)
        self.assertEqual(self.api.get('/api/dashboard/').data['kpis']['stock'], 12)
        self.assertEqual(self.api.get('/api/dashboard/').data['kpis']['chiffre_affaires'], 0)
        detail = self.api.get(f'/api/stock-detail/?lot={self.lot.pk}').data
        self.assertEqual(detail['stock_initial'], 12)
        self.assertEqual(detail['naissances'], 12)
        self.assertEqual(detail['stock_restant'], 12)
        lot_detail = self.api.get(f'/api/lots/{self.lot.pk}/').data
        self.assertEqual(lot_detail['stock'], 12)
        self.assertEqual(lot_detail['mouvements'][0]['mort_nes'], 2)
        self.assertEqual(lot_detail['mouvements'][0]['note'], 'Portée normale')

    def test_birth_and_exits_share_the_existing_animal_sale_flow(self):
        purchase = Achat.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            date=date(2026, 9, 1), quantite=5,
            prix_total=Decimal('100'), prix_unitaire=Decimal('20'),
        )
        Mouvement.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            type_mouvement='ACHAT', quantite=purchase.quantite,
        )
        self.assertEqual(self.birth().status_code, 201)
        for kind, count in [('MORTALITE', 2), ('VENTE', 5), ('DON', 1), ('VOL', 1)]:
            response = self.exit(kind, count)
            self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(self.lot.stock, 8)
        self.assertEqual(get_lot_stock(self.lot), 8)
        self.assertEqual(Vente.objects.count(), 1)
        self.assertEqual(Vente.objects.get().quantite, 5)
        self.assertEqual(self.api.get('/api/dashboard/').data['kpis']['stock'], 8)
        self.assertEqual(self.api.get(f'/api/stock-detail/?lot={self.lot.pk}').data['stock_restant'], 8)
        self.assertEqual(self.exit('VENTE', 9).status_code, 400)
        self.assertEqual(Vente.objects.count(), 1)

    def test_invalid_birth_data_and_sale_fields_are_rejected(self):
        for value in [0, -1, 1.5, '1.5', True, None]:
            response = self.birth(value)
            self.assertEqual(response.status_code, 400, (value, response.data))
        for changes in (
            {'mort_nes': -1},
            {'mort_nes': 1.5},
            {'client': self.client.pk},
            {'prix_unitaire': '10.00'},
        ):
            response = self.birth(**changes)
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.exit('MORTALITE', 1, mort_nes=2).status_code, 400)
        self.assertEqual(Mouvement.objects.count(), 0)

    def test_deleting_birth_restores_stock_and_refuses_negative_remainder(self):
        first = self.birth(12)
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(self.exit('VENTE', 10).status_code, 201)
        self.assertEqual(self.api.delete(
            f"/api/mouvements/delete/{first.data['id']}/",
        ).status_code, 400)
        self.assertEqual(self.lot.stock, 2)
        self.assertEqual(Vente.objects.count(), 1)
        second = self.birth(11)
        self.assertEqual(second.status_code, 201, second.data)
        removed = self.api.delete(f"/api/mouvements/delete/{first.data['id']}/")
        self.assertEqual(removed.status_code, 200, removed.data)
        self.assertEqual(self.lot.stock, 1)
        self.assertEqual(get_lot_stock(self.lot), 1)
        self.assertEqual(Vente.objects.count(), 1)

    def test_birth_is_tenant_scoped_for_creation_history_and_deletion(self):
        own = self.birth()
        self.assertEqual(own.status_code, 201, own.data)
        other_species = Espece.objects.create(
            nom='Porc', exploitation=self.other.exploitation,
        )
        foreign_lot = Lot.objects.create(
            nom='Autre', espece=other_species,
            exploitation=self.other.exploitation, date_debut=date(2026, 9, 1),
        )
        self.assertIn(self.birth(lot=foreign_lot.pk).status_code, (403, 404))
        self.assertEqual(self.api.get(f'/api/lots/{foreign_lot.pk}/').status_code, 403)
        self.api.force_authenticate(self.other)
        self.assertEqual(self.api.delete(
            f"/api/mouvements/delete/{own.data['id']}/",
        ).status_code, 403)
        self.assertEqual(self.api.get(f'/api/lots/{self.lot.pk}/').status_code, 403)
        self.assertEqual(self.api.get('/api/mouvements/').data, [])
        self.assertEqual(Mouvement.objects.filter(type_mouvement='NAISSANCE').count(), 1)

    def test_growth_and_egg_effectif_include_birth_without_creating_eggs(self):
        Achat.objects.create(
            lot=self.lot, exploitation=self.user.exploitation,
            date=date(2026, 9, 1), quantite=10,
            prix_total=Decimal('100'), prix_unitaire=Decimal('10'),
        )
        weighed = self.api.post('/api/production/pesees/', {
            'lot': self.lot.pk,
            'pesee_at': datetime(2026, 9, 30, 8, tzinfo=dt_timezone.utc).isoformat(),
            'nombre_animaux_peses': 10,
            'poids_total_kg': '20.000',
        }, format='json')
        self.assertEqual(weighed.status_code, 201, weighed.data)
        self.assertEqual(self.birth().status_code, 201)
        after = self.api.get(f'/api/production/pesees/?lot={self.lot.pk}')
        self.assertEqual(after.status_code, 200, after.data)
        self.assertEqual(after.data['effectif_actuel'], 22)
        self.assertEqual(after.data['biomasse_estimee_kg'], Decimal('44.000'))
        self.assertEqual(MouvementOeufs.objects.count(), 0)
        self.assertEqual(get_egg_stock(self.user.exploitation), 0)
        self.assertEqual(get_live_birds(self.lot), 22)
