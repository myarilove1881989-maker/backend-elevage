from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import (User,Lot,Espece,Mouvement,Client,Vente,Depense,Payment,Lettrage,
    PeseeProduction,AuditEvent,TerrainSubmission,TerrainOutcome)
from core.tests.functional_fixtures import operator_for_tests
from core.tests.test_terrain_operations import OperationFixture

class LotLifecycleTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='lifecycle-owner')
        self.operator=operator_for_tests(self.owner,'lifecycle-operator',['weights.create','expenses.create','cash.create'])
        self.farm=self.owner.exploitation;self.today=timezone.localdate()
        species=Espece.objects.create(nom='Synthétique',exploitation=self.farm)
        self.lot=Lot.objects.create(exploitation=self.farm,espece=species,nom='Cycle test',date_debut=self.today-timedelta(days=20))
        Mouvement.objects.create(lot=self.lot,exploitation=self.farm,type_mouvement='ACHAT',quantite=20,date=self.today-timedelta(days=15))
        self.customer=Client.objects.create(nom='Client test',exploitation=self.farm)
        self.sale=Vente.objects.create(lot=self.lot,client=self.customer,quantite=2,prix_unitaire=100,date=self.today-timedelta(days=2))
        self.expense=Depense.objects.create(lot=self.lot,montant=20,date=self.today-timedelta(days=2))
        payment=Payment.objects.create(client=self.customer,exploitation=self.farm,montant=50,date=self.today)
        Lettrage.objects.create(payment=payment,vente=self.sale,montant=50)
        self.api=APIClient();self.api.force_authenticate(self.owner);self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
    def command(self,action='CLOSE',**changes):
        self.farm.refresh_from_db()
        data={'action':action,'farm':self.farm.pk,'expected_revision':self.farm.business_revision,'confirm':True,'date_fin':str(self.today)}
        data.update(changes);return self.api.post(f'/api/lots/{self.lot.pk}/lifecycle/',data,format='json')
    def test_close_preserves_every_business_row_finances_stock_and_cache(self):
        models=[Mouvement,Vente,Depense,Payment,Lettrage]
        before={m:list(m._base_manager.order_by('pk').values()) for m in models}
        debt=self.api.get('/api/dettes-clients/').data
        kpis=self.api.get('/api/dashboard/').data['kpis']
        response=self.command();self.assertEqual(response.status_code,200,response.data)
        self.assertTrue(response.data['closed']);self.assertEqual(response.data['stock_animaux'],20)
        self.assertEqual(response.data['creances'],150)
        for model in models:self.assertEqual(before[model],list(model._base_manager.order_by('pk').values()))
        self.assertEqual(self.api.get('/api/dettes-clients/').data,debt)
        after=self.api.get('/api/dashboard/').data['kpis']
        self.assertEqual(after['stock'],0);self.assertEqual(after['stock_lots_clotures'],20)
        self.assertEqual(after['chiffre_affaires'],kpis['chiffre_affaires']);self.assertEqual(after['depenses'],kpis['depenses'])
        self.assertEqual(self.api.get('/api/lots/?lot_state=active').data,[])
        self.assertEqual(self.api.get('/api/lots/?lot_state=closed').data[0]['id'],self.lot.pk)
        from core.cache_views import stock_snapshots
        self.assertEqual(str(stock_snapshots(self.farm.pk,[self.lot.pk])[0]['date_fin']),str(self.today))
        event=AuditEvent.objects.get(entity_type='core.lot',entity_id=str(self.lot.pk),reason_code='LOT_CLOSE')
        self.assertEqual(event.actor_user_id,self.owner.pk)
        self.assertEqual(self.api.get(f'/api/lots/{self.lot.pk}/').status_code,200)
    def test_closed_new_writes_blocked_but_payment_allowed(self):
        self.assertEqual(self.command().status_code,200)
        for user in [self.owner,self.operator]:
            self.api.force_authenticate(user)
            result=self.api.post('/api/production/pesees/',{'lot':self.lot.pk,'nombre_animaux_peses':2,'poids_total_kg':'1.000'},format='json')
            self.assertEqual(result.status_code,400,result.data)
            result=self.api.post('/api/depenses/create/',{'lot':self.lot.pk,'montant':'5'},format='json')
            self.assertEqual(result.status_code,400,result.data)
        self.api.force_authenticate(self.owner)
        result=self.api.post('/api/payments/create/',{'client':self.customer.pk,'vente':self.sale.pk,'montant':'25'},format='json')
        self.assertEqual(result.status_code,201,result.data);self.sale.refresh_from_db();self.assertEqual(self.sale.reste_a_payer,125)
        self.assertEqual(PeseeProduction.objects.count(),0)
    def test_reopen_preserves_production_status_and_is_audited(self):
        self.lot.type_production='OEUFS';self.lot.statut_production='PONTE';self.lot.save()
        self.assertEqual(self.command(reason='Fin de cycle').status_code,200)
        self.assertEqual(self.command('REOPEN',reason='Reprise autorisée').status_code,200)
        self.lot.refresh_from_db();self.assertIsNone(self.lot.date_fin);self.assertEqual(self.lot.statut_production,'PONTE')
        self.assertTrue(AuditEvent.objects.filter(reason_code='LOT_REOPEN',reason_text='Reprise autorisée').exists())
    def test_owner_scope_confirmation_dates_and_revision(self):
        for values in [{'confirm':False},{'date_fin':str(self.today+timedelta(days=1))},{'date_fin':str(self.today-timedelta(days=3))},{'expected_revision':-1}]:
            self.assertEqual(self.command(**values).status_code,400)
        self.lot.refresh_from_db();self.assertIsNone(self.lot.date_fin)
        self.api.force_authenticate(self.operator);self.assertEqual(self.command().status_code,403)
        other=User.objects.create_user(username='lifecycle-other');self.api.force_authenticate(other)
        self.assertEqual(self.command().status_code,404)
        self.api.force_authenticate(self.owner);self.api.credentials()
        self.assertEqual(self.command().status_code,403)
    def test_closed_correction_requires_extra_explicit_authorization(self):
        weight=PeseeProduction.objects.create(lot=self.lot,exploitation=self.farm,nombre_animaux_peses=2,poids_total_kg=1)
        self.command();self.farm.refresh_from_db()
        url=f'/api/maintenance/weights/{weight.pk}/'
        body={'farm':self.farm.pk,'expected_revision':self.farm.business_revision,'confirm':True,'reason':'Correction documentée','values':{'poids_total_kg':'2.000'}}
        self.assertEqual(self.api.patch(url,body,format='json').status_code,400)
        body['allow_closed']=True
        self.assertEqual(self.api.patch(url,body,format='json').status_code,200)
        weight.refresh_from_db();self.assertEqual(weight.poids_total_kg,2)
        self.lot.refresh_from_db();self.assertIsNotNone(self.lot.date_fin)

    def test_closed_production_ratios_stop_accumulating_hen_days(self):
        from core.models import Achat
        from core.egg_services import get_hen_days
        first=self.today-timedelta(days=10);end=self.today-timedelta(days=2)
        Achat.objects.create(exploitation=self.farm,lot=self.lot,quantite=20,prix_total=100,prix_unitaire=5,date=first)
        self.lot.date_fin=end;self.lot.save()
        self.assertEqual(get_hen_days(self.lot,first,self.today),get_hen_days(self.lot,first,end))
        self.assertEqual(get_hen_days(self.lot,self.today,self.today),0)

class ClosedOfflineTests(OperationFixture,TestCase):
    def test_delayed_signed_operation_is_kept_for_review_not_applied_or_erased(self):
        original=self.terrain('MORTALITE',{'lot_ref':self.lot_ref(),'quantite':2})
        self.lot.date_fin=timezone.localdate();self.lot.save()
        receipt=self.apply(original)
        self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION',receipt)
        self.assertEqual(receipt['reason_code'],'LOT_CLOSED',receipt)
        row=TerrainSubmission.objects.get(client_operation_id=original['client_operation_id'])
        self.assertEqual(row.payload,original['payload']);self.assertEqual(row.author_user_id,self.jean.pk)
        self.assertEqual(self.lot.stock,20)
        again=self.apply(original);self.assertEqual(again['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(TerrainSubmission.objects.count(),1)
