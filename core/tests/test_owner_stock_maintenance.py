from django.test import TestCase
from core.tests import test_egg_fifo_api as fifo
from core.models import Vente,VenteOeufs,CollecteOeufs,Payment,Lettrage,AuditEvent

class OwnerStockMaintenanceTests(TestCase):
    def setUp(self):
        self.fixture=fifo.EggFifoApiTests('test_single_collection_and_initial_losses')
        self.fixture.setUp();self.owner=self.fixture.owner;self.api=self.fixture.api
        self.api.force_authenticate(self.owner)
        self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
    def body(self,values=None):
        self.owner.exploitation.refresh_from_db()
        return {'farm':self.owner.exploitation_id,'confirm':True,'reason':'Correction de recette synthétique',
            'expected_revision':self.owner.exploitation.business_revision,'values':values or {}}
    def test_sale_correction_is_atomic_and_fifo_balances_follow(self):
        source=self.fixture.collect(100)
        sale=self.fixture.sell(20);self.assertEqual(sale.status_code,201,sale.data)
        pk=sale.data['id'];url=f'/api/maintenance/egg_sales/{pk}/'
        result=self.api.patch(url,self.body({'nombre_oeufs':30,'montant_total':'2500.00'}),format='json')
        self.assertEqual(result.status_code,200,result.data)
        obj=VenteOeufs.objects.get(pk=pk)
        self.assertEqual(obj.nombre_oeufs,30);self.assertEqual(obj.vente.montant_total,2500)
        state=self.fixture.stock();self.assertTrue(state['origines_completes']);self.assertEqual(state['stock_global'],70)
        self.assertEqual(state['collectes'][0]['restant'],70)
        failure=self.api.patch(url,self.body({'nombre_oeufs':200,'montant_total':'5000'}),format='json')
        self.assertEqual(failure.status_code,400);obj.refresh_from_db();self.assertEqual(obj.nombre_oeufs,30)
        self.assertEqual(self.fixture.stock()['stock_global'],70)
        blocked=self.api.delete(f'/api/maintenance/collections/{source}/',self.body(),format='json')
        self.assertEqual(blocked.status_code,400)
        deleted=self.api.delete(url,self.body(),format='json');self.assertEqual(deleted.status_code,200,deleted.data)
        self.assertEqual(self.fixture.stock()['stock_global'],100)
        deleted=self.api.delete(f'/api/maintenance/collections/{source}/',self.body(),format='json')
        self.assertEqual(deleted.status_code,200,deleted.data);self.assertEqual(self.fixture.stock()['stock_global'],0)
        self.assertTrue(AuditEvent.objects.filter(action='DELETE',entity_type='core.venteoeufs',entity_id=str(pk)).exists())
    def test_paid_sale_and_paid_lot_preserve_money_and_stock(self):
        self.fixture.collect(100);sale=self.fixture.sell(20)
        eggs=VenteOeufs.objects.get(pk=sale.data['id'])
        payment=Payment.objects.create(client=self.fixture.client,exploitation=self.owner.exploitation,montant=500,date=eggs.vente.date)
        Lettrage.objects.create(payment=payment,vente=eggs.vente,montant=500)
        for method in ('patch','delete'):
            result=getattr(self.api,method)(f'/api/maintenance/egg_sales/{eggs.pk}/',self.body({'nombre_oeufs':30,'montant_total':'3000'}),format='json')
            self.assertEqual(result.status_code,400,result.data)
        result=self.api.delete(f'/api/maintenance/lots/{self.fixture.lot.pk}/',self.body(),format='json')
        self.assertEqual(result.status_code,400,result.data)
        self.assertEqual(self.fixture.stock()['stock_global'],80)
        self.assertEqual(Vente.objects.get().montant_paye,500)
        self.assertEqual(Vente.objects.get().reste_a_payer,1500)
