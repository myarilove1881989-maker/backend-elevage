from datetime import datetime, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import User, Espece, Client, Lot, CategorieDepense, Depense


class EggJourneyTests(TestCase):
    def test_purchase_collection_sale_payment_statistics_and_tenant_isolation(self):
        self.run_journey()

    def test_default_statistics_keep_local_day_after_midnight_before_utc_midnight(self):
        instant=datetime(2026,10,7,22,30,tzinfo=datetime_timezone.utc)
        with timezone.override('Europe/Paris'), patch('django.utils.timezone.now',return_value=instant), \
                patch('core.views.now',return_value=instant):
            # Same real purchase/collection/sale/cash journey; still expects the
            # 5,000 margin without explicit dates, despite the UTC previous day.
            self.run_journey()

    def run_journey(self):
        user = User.objects.create_user(username='journey')
        other = User.objects.create_user(username='other-journey')
        api = APIClient()
        api.force_authenticate(user)
        species = Espece.objects.create(nom='Poulet', exploitation=user.exploitation)
        customer = Client.objects.create(nom='Client', exploitation=user.exploitation)
        today = timezone.localdate()
        purchase = api.post('/api/achats/create/', {
            'nom_lot': 'Ponte intégration', 'espece': species.pk, 'quantite': 100,
            'prix_total': '100000.00', 'prix_unitaire': '1000.00',
            'date': today.isoformat(), 'type_production': 'OEUFS', 'statut_production': 'PONTE',
        }, format='json')
        self.assertEqual(purchase.status_code, 201, purchase.data)
        lot_id = purchase.data['lot_id']
        collection = api.post('/api/oeufs/collectes/', {
            'lot': lot_id, 'collecte_at': timezone.now().isoformat(),
            'nombre_collecte': 100, 'nombre_casses': 10,
        }, format='json')
        self.assertEqual(collection.status_code, 201, collection.data)
        sale = api.post('/api/oeufs/ventes/', {
            'lot': lot_id, 'client': customer.pk, 'conditionnement': 'PLATEAU',
            'nombre_conditionnements': 2, 'prix_unitaire_conditionnement': '3000.00',
            'date': today.isoformat(),
        }, format='json')
        self.assertEqual(sale.status_code, 201, sale.data)
        category = CategorieDepense.objects.create(nom='Alimentation', exploitation=user.exploitation)
        expense = Depense.objects.create(lot_id=lot_id, categorie=category, date=today, montant=1000)
        feed = api.post('/api/oeufs/alimentation/', {
            'lot': lot_id, 'date': today.isoformat(), 'quantite_kg': '10.000',
            'prix_kg': '100.00', 'depense': expense.pk,
        }, format='json')
        self.assertEqual(feed.status_code, 201, feed.data)
        for amount, expected_status in [(2000, 'PARTIEL'), (4000, 'PAYE')]:
            payment = api.post('/api/payments/create/', {
                'client': customer.pk, 'vente': sale.data['vente'], 'montant': amount,
            }, format='json')
            self.assertEqual(payment.status_code, 201, payment.data)
            detail = api.get(f"/api/oeufs/ventes/{sale.data['id']}/")
            self.assertEqual(detail.data['statut'], expected_status)
        stats = api.get(f'/api/oeufs/statistiques/?lot={lot_id}')
        self.assertEqual(stats.status_code, 200, stats.data)
        self.assertEqual(stats.data['stock_oeufs'], 30)
        self.assertEqual(stats.data['nombre_poules_vivantes'], 100)
        self.assertEqual(Decimal(stats.data['marge_oeufs']), Decimal('5000'))
        self.assertEqual(Decimal(stats.data['chiffre_affaires_oeufs']), Decimal('6000'))
        self.assertEqual(Lot.objects.get(pk=lot_id).stock, 100)
        api.force_authenticate(other)
        for path in [
            f'/api/oeufs/statistiques/?lot={lot_id}',
            f"/api/oeufs/collectes/{collection.data['id']}/",
            f"/api/oeufs/ventes/{sale.data['id']}/",
            f"/api/oeufs/alimentation/{feed.data['id']}/",
        ]:
            self.assertEqual(api.get(path).status_code, 404, path)
            if 'statistiques' not in path:
                self.assertEqual(api.delete(path).status_code, 404, path)
        for path in ['collectes', 'ventes', 'alimentation']:
            self.assertEqual(api.get(f'/api/oeufs/{path}/').data, [])
