import uuid
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.test import TestCase,TransactionTestCase
from django.db import connection,transaction,DatabaseError,close_old_connections
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import Client,Vente,Payment,Lettrage,Depense,Mouvement,Lot,User,MembershipPermission,ExploitationMembership
from core.views import client_totals
from pisciculture.models import Harvest,HarvestMovement,Event,Position,Command
from pisciculture.tests.test_phase2b import StockFixture
from pisciculture.tests.test_phase1 import PREFIX
from pisciculture.commerce import balance

class CommerceFixture(StockFixture):
    def setUp(self):
        super().setUp();self.customer=Client.objects.create(exploitation=self.farm,nom='Client existant')
        self.at=(timezone.now()-timezone.timedelta(minutes=30)).isoformat()
    def review(self,at=None,h=None,**origin):
        params={'as_of':at or self.at,**({'harvest':str(h.pk)} if h else {'installation':str(self.site.pk),'cohort':str(self.a.pk)}),**origin}
        r=self.api.get(PREFIX+'commerce-check/',params);self.assertEqual(r.status_code,200,r.data)
        return {'health_token':r.data['health_token'],'human_verified':True,'verification_reason':'Vérification humaine documentaire synthétique'}
    def body(self,**kw):
        at=kw.pop('occurred_at',self.at)
        return {'cohort':str(self.a.pk),'installation':str(self.site.pk),'live_count':100,'counted':True,'mass_kg':'100.000000','mass_quality':'MEASURED','stock_unit':'KG','destination':'STOCK','storage_place':'Glacière','storage_mode':'Déclaration utilisateur','occurred_at':at,'review':self.review(at),**kw}
    def harvest(self,**kw):
        r=self.post('harvests/',**self.body(**kw));self.assertEqual(r.status_code,201,r.data);return Harvest.objects.get(pk=r.data['id'])
    def sale_body(self,h,n='30.000000',**kw):
        at=kw.pop('occurred_at',timezone.now().isoformat())
        return {'harvest':str(h.pk),'quantity':n,'price':{'client':self.customer.pk,'unit_price':'2.50'},'occurred_at':at,'review':self.review(at,h),**kw}
    def sell(self,h,n='30.000000',**kw):return self.post('harvest-sales/',**self.sale_body(h,n,**kw))
    def loss_post(self,h,n='5.000000',**kw):return self.post('postharvest-losses/',harvest=str(h.pk),quantity=n,occurred_at=timezone.now().isoformat(),reason='Perte de conservation constatée',**kw)

class CommerceTests(CommerceFixture,TestCase):
    def test_harvest_single_debit_no_mortality_or_finance(self):
        h=self.harvest();self.assertEqual(self.state()['live_count'],900);self.assertEqual(h.stock_event.type,'HARVEST');self.assertEqual(balance(h)['available_quantity'],'100.000000')
        self.assertFalse(Vente.objects.exists());self.assertFalse(Depense.objects.exists());self.assertFalse(Mouvement.objects.exists());self.assertFalse(Lot.objects.exists())
    def test_total_harvest_and_unknown_weight_no_conversion(self):
        h=self.harvest(live_count=600,mass_kg=None,mass_quality='UNKNOWN',stock_unit='PIECE');self.assertEqual(self.state()['live_count'],400);self.assertIsNone(balance(h)['available_mass_kg']);self.assertEqual(balance(h)['available_count'],600)
    def test_estimate_remains_estimate_and_cannot_back_kg_stock(self):
        h=self.harvest(mass_kg='88.200000',mass_quality='ESTIMATED',stock_unit='PIECE');self.assertIsNone(balance(h)['available_mass_kg']);self.assertEqual(h.mass_kg,Decimal('88.2'))
        self.assertEqual(self.post('harvests/',**self.body(mass_quality='ESTIMATED')).status_code,400)
    def test_exact_count_required_no_float_or_implicit_weight_conversion(self):
        for delta in [{'live_count':None},{'live_count':1.2},{'counted':False},{'counted':1},{'counted':'true'}]:self.assertEqual(self.post('harvests/',**self.body(**delta)).status_code,400)
    def test_direct_sale_atomic_unique_invoice_and_no_fake_lot(self):
        h=self.harvest(destination='DIRECT',direct_sale={'client':self.customer.pk,'unit_price':'3.10'});self.assertEqual(Vente.objects.count(),1);s=Vente.objects.get();self.assertIsNone(s.lot_id);self.assertEqual(s.montant_total,Decimal('310'));self.assertEqual(balance(h)['status'],'EXHAUSTED');self.assertEqual(self.state()['live_count'],900)
        self.assertEqual(self.sell(h,'1.000000').status_code,409);self.assertFalse(Lot.objects.exists())
    def test_bad_direct_customer_rolls_back_live_debit(self):
        stranger=User.objects.create_user(username='foreign-customer');c=Client.objects.create(exploitation=stranger.exploitation,nom='Autre')
        self.assertEqual(self.post('harvests/',**self.body(destination='DIRECT',direct_sale={'client':c.pk,'unit_price':'2.00'})).status_code,404);self.assertFalse(Harvest.objects.exists());self.assertEqual(self.state()['live_count'],1000)
    def test_100_30_45_25_and_no_forced_count_or_duplicate_live_debit(self):
        h=self.harvest();self.assertEqual(self.sell(h).status_code,201);self.assertEqual(self.sell(h,'45.000000').status_code,201)
        self.assertEqual(balance(h)['available_quantity'],'25.000000');self.assertIsNone(balance(h)['available_count']);self.assertEqual(self.state()['live_count'],900);self.assertEqual(Vente.objects.count(),2);self.assertEqual(client_totals(self.customer),(Decimal('187.50'),Decimal('0')))
        self.assertEqual(self.sell(h,'25.000001').status_code,409);self.assertEqual(Vente.objects.count(),2)
    def test_loss_only_postharvest_no_expense_or_mortality(self):
        h=self.harvest();r=self.loss_post(h);self.assertEqual(r.status_code,201,r.data);self.assertEqual(balance(h)['available_quantity'],'95.000000');self.assertEqual(self.state()['live_count'],900);self.assertEqual(self.state()['pending'],[]);self.assertFalse(Depense.objects.exists());self.assertFalse(Vente.objects.exists())
    def test_piece_and_secondary_measures_consistency(self):
        h=self.harvest(stock_unit='PIECE');self.assertEqual(self.sell(h,'10.500000').status_code,400)
        self.assertEqual(self.sell(h,'10.000000',count=9).status_code,400)
        self.assertEqual(self.sell(h,'10.000000',count=10,mass_kg='10.000000').status_code,201)
        self.assertEqual(balance(h)['available_count'],90);self.assertEqual(balance(h)['available_mass_kg'],'90.000000')
    def test_payments_partial_rest_and_existing_client_balance(self):
        h=self.harvest();r=self.sell(h);self.assertEqual(r.status_code,201,r.data);sale=Vente.objects.get()
        for amount in ['20.00','55.00']:
            p=self.post('commerce-payments/',sale=sale.pk,amount=amount,occurred_at=timezone.now().isoformat());self.assertEqual(p.status_code,201,p.data)
        self.assertEqual(Payment.objects.count(),2);self.assertEqual(Lettrage.objects.count(),2);self.assertEqual(client_totals(self.customer),(Decimal('75'),Decimal('75')))
        invoice=self.api.get(PREFIX+'commerce-invoices/'+str(sale.pk)+'/');self.assertEqual(invoice.data['statut'],'PAYE');self.assertEqual(invoice.data['reste'],'0.00')
        self.assertEqual(self.post('commerce-payments/',sale=sale.pk,amount='0.01',occurred_at=timezone.now().isoformat()).status_code,409)
    def test_new_client_uses_existing_model_uuid_once(self):
        body={'operation_uuid':str(uuid.uuid4()),'customer':{'nom':'Nouveau','telephone':'010203'},'occurred_at':self.at}
        a=self.post('commerce-clients/',**body);b=self.post('commerce-clients/',**body);self.assertEqual(a.status_code,201,a.data);self.assertEqual(a.data,b.data);self.assertEqual(Client.objects.filter(nom='Nouveau').count(),1)
    def test_harvest_replay_stable_after_revision_and_payload_collision(self):
        data={**self.body(),'operation_uuid':str(uuid.uuid4())};a=self.post('harvests/',**data);b=self.post('harvests/',**data);self.assertEqual(a.status_code,201,a.data);self.assertEqual(a.data,b.data);self.assertEqual(self.state()['live_count'],900)
        self.assertEqual(self.post('harvests/',**{**data,'live_count':99}).status_code,409)
    def test_sale_payment_replay_never_duplicates_financial_entries(self):
        h=self.harvest();data={**self.sale_body(h),'operation_uuid':str(uuid.uuid4())};a=self.post('harvest-sales/',**data);b=self.post('harvest-sales/',**data);self.assertEqual(a.status_code,201,a.data);self.assertEqual(a.data,b.data);self.assertEqual(Vente.objects.count(),1)
        sale=Vente.objects.get();p={'operation_uuid':str(uuid.uuid4()),'sale':sale.pk,'amount':'20.00','occurred_at':timezone.now().isoformat()}
        self.assertEqual(self.post('commerce-payments/',**p).status_code,201);self.assertEqual(self.post('commerce-payments/',**p).status_code,201);self.assertEqual(Payment.objects.count(),1);self.assertEqual(sale.montant_paye,Decimal('20'))
    def test_stale_health_snapshot_and_human_confirmation_mandatory(self):
        data=self.body();self.loss(1,cohort=str(self.b.pk));self.assertEqual(self.post('harvests/',**data).status_code,409)
        data=self.body();data['review']['human_verified']=False;self.assertEqual(self.post('harvests/',**data).status_code,400)
    def test_backdate_before_harvest_or_stock_prefix_refused(self):
        h=self.harvest();at=(timezone.now()-timezone.timedelta(hours=1)).isoformat();self.assertEqual(self.sell(h,occurred_at=at).status_code,400)
        self.assertEqual(self.post('harvests/',**self.body(live_count=601)).status_code,409)
    def test_harvest_respects_pending_unattributed_losses(self):
        self.loss(10,occurred_at=(timezone.now()-timezone.timedelta(minutes=45)).isoformat());self.assertEqual(self.post('harvests/',**self.body(live_count=595)).status_code,409)
    def test_correction_financial_dependencies_refused_and_request_audited(self):
        h=self.harvest();r=self.sell(h);target=r.data['id'];sale=Vente.objects.get();self.assertEqual(self.post('commerce-payments/',sale=sale.pk,amount='20.00',occurred_at=timezone.now().isoformat()).status_code,201);p={'harvest':str(h.pk),'target':target,'occurred_at':timezone.now().isoformat(),'reason':'Erreur à examiner'}
        self.assertEqual(self.post('commerce-cancellations/',**p).status_code,409)
        c=self.post('commerce-corrections/',**p);self.assertEqual(c.status_code,201,c.data);self.assertFalse(c.data['financial_effect']);self.assertEqual(Vente.objects.count(),1);self.assertEqual(balance(h)['available_quantity'],'70.000000')
    def test_untouched_harvest_compensated_once_and_loss_void(self):
        h=self.harvest();r=self.loss_post(h);v=self.post('commerce-cancellations/',harvest=str(h.pk),target=r.data['id'],reason='Correction perte',occurred_at=timezone.now().isoformat());self.assertEqual(v.status_code,201,v.data);self.assertEqual(balance(h)['available_quantity'],'100.000000')
        other=self.harvest();v=self.post('commerce-cancellations/',harvest=str(other.pk),reason='Erreur récolte sans suite',occurred_at=timezone.now().isoformat());self.assertEqual(v.status_code,201,v.data);self.assertEqual(self.state()['live_count'],900);self.assertEqual(balance(other)['status'],'CANCELED')
    def test_foreign_references_tenant_isolation(self):
        h=self.harvest();u=User.objects.create_user(username='other-farm');api=APIClient();api.force_authenticate(u)
        from pisciculture.models import Activity
        Activity.objects.create(exploitation=u.exploitation,enabled=True)
        self.assertEqual(api.get(PREFIX+'harvests/'+str(h.pk)+'/').status_code,404);self.assertEqual(api.get(PREFIX+'harvests/').data['results'],[])
    def test_history_filters_pagination_and_bad_cursor(self):
        h=self.harvest();self.sell(h);self.loss_post(h)
        r=self.api.get(PREFIX+'commerce-history/',{'limit':1,'harvest':str(h.pk)});self.assertEqual(r.status_code,200,r.data);self.assertIsNotNone(r.data['next_cursor'])
        self.assertEqual(self.api.get(PREFIX+'commerce-history/',{'limit':1,'harvest':str(h.pk),'cursor':r.data['next_cursor']}).status_code,200)
        self.assertEqual(self.api.get(PREFIX+'commerce-history/',{'limit':2,'cursor':r.data['next_cursor']}).status_code,400)
    @skipUnless(connection.vendor=='postgresql','PostgreSQL required')
    def test_immutable_sql_facts(self):
        h=self.harvest()
        for table in ['pisciculture_harvest']:
            with self.assertRaises(DatabaseError),transaction.atomic():
                with connection.cursor() as q:q.execute('UPDATE '+table+' SET note=note')

    def treatment(self,**kw):
        from core.supply_models import SupplyProduct
        v=SupplyProduct.objects.create(exploitation=self.farm,name='Produit sanitaire fictif',kind='VACCINE')
        r=self.post('health-treatments/',installation=str(self.site.pk),occurred_at=(timezone.now()-timezone.timedelta(minutes=90)).isoformat(),supply_product=str(v.pk),quantity='1.000000',unit='dose',administration_status='ADMINISTERED',reason='Constat synthétique',**kw)
        self.assertEqual(r.status_code,201,r.data);return r
    def test_documented_active_delay_blocks_even_with_human_checked(self):
        self.treatment(withdrawal_days=2,withdrawal_source='Document fictif')
        data=self.body();r=self.post('harvests/',**data);self.assertEqual(r.status_code,409,r.data);self.assertEqual(r.data['error']['code'],'HEALTH_RECORDED_RESTRICTION');self.assertEqual(self.state()['live_count'],1000)
    def test_unknown_delay_explicit_human_review_retained(self):
        self.treatment();h=self.harvest();self.assertIsNone(h.health_review['sale_authorized']);self.assertEqual(h.health_review['status'],'UNKNOWN_WITHDRAWAL');self.assertTrue(h.health_review['human_verified']);self.assertTrue(h.health_review['matches'])
        r=self.sell(h);self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['health_review']['status'],'UNKNOWN_WITHDRAWAL')
    def test_transferred_origin_keeps_traceability_review(self):
        self.treatment(withdrawal_days=0,withdrawal_source='Document fictif')
        r=self.post('transfers/',**self.transfer_body(50,at=(timezone.now()-timezone.timedelta(minutes=60)).isoformat()));self.assertEqual(r.status_code,201,r.data)
        review=self.review(self.at,installation=str(self.other_site.pk));data=self.body(installation=str(self.other_site.pk),live_count=20,review=review)
        r=self.post('harvests/',**data);self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['health_review']['status'],'TRACEABILITY_REVIEW')
    def test_current_health_correction_invalidates_preview(self):
        t=self.treatment();data=self.body()
        from core.supply_models import SupplyProduct
        v=SupplyProduct.objects.get(name='Produit sanitaire fictif')
        replacement={'installation':str(self.site.pk),'occurred_at':(timezone.now()-timezone.timedelta(minutes=90)).isoformat(),'supply_product':str(v.pk),'quantity':'1.000000','unit':'dose','administration_status':'ADMINISTERED','reason':'Correction synthétique','withdrawal_days':2,'withdrawal_source':'Document fictif'}
        self.assertEqual(self.post('health-corrections/',target=t.data['id'],reason='Source vérifiée',treatment=replacement).status_code,201)
        self.assertEqual(self.post('harvests/',**data).status_code,409);self.assertEqual(self.state()['live_count'],1000)
    def test_permissions_and_revocation_before_replay(self):
        data={**self.body(),'operation_uuid':str(uuid.uuid4())}
        operator=User.objects.create_user(username='commerce-operator',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=operator,exploitation=self.farm)
        MembershipPermission.objects.create(membership=member,code='aquatic.read')
        self.api.force_authenticate(operator);self.assertEqual(self.post('harvests/',**data).status_code,403)
        MembershipPermission.objects.create(membership=member,code='aquatic.harvest.create')
        data['review']=self.review();r=self.post('harvests/',**data);self.assertEqual(r.status_code,201,r.data)
        MembershipPermission.objects.create(membership=member,code='cash.create')
        self.assertIn('cash.create',self.api.get(PREFIX+'capabilities/').data['commerce_write'])
        member.is_active=False;member.save();self.assertEqual(self.post('harvests/',**data).status_code,403)
    def test_financial_existing_routes_balance_invoice_and_admin_exports(self):
        h=self.harvest();self.sell(h);sale=Vente.objects.get()
        self.assertEqual(self.api.get('/api/dettes-clients/').data[0]['total_facture'],75)
        self.assertEqual(self.api.get('/api/clients/total-dettes/').data['total_dettes'],75)
        r=self.api.get('/api/clients/'+str(self.customer.pk)+'/ventes/')
        self.assertEqual(r.status_code,200,r.data);self.assertEqual(r.data[0]['produit_vendu'],'POISSONS');self.assertEqual(r.data[0]['priced_quantity'],'30.000000')
        from core.admin import VenteAdmin,object_farm_id
        self.assertEqual(object_farm_id(sale),self.farm.pk)
        self.assertEqual(sale.accounting_exploitation_id,self.farm.pk)
        self.assertEqual(sale.montant_total,Decimal('75'))
        from django.urls import reverse
        from io import BytesIO
        from openpyxl import load_workbook
        admin=User.objects.create_superuser(username='commerce-admin',email='commerce@example.com',password='synthetic-local-only')
        self.client.force_login(admin)
        response=self.client.get(reverse('admin-export-statistics'));self.assertEqual(response.status_code,200)
        wb=load_workbook(BytesIO(response.content),read_only=True)
        rows=list(wb['Ventes'].values);self.assertEqual(len(rows),2);self.assertEqual(rows[1][8],75);self.assertEqual(rows[1][-1],'KG')
        wb.close()

    def test_price_cents_round_half_up_exact_and_decimal_strings_required(self):
        h=self.harvest();r=self.sell(h,'0.333333',price={'client':self.customer.pk,'unit_price':'3.00'});self.assertEqual(r.status_code,201,r.data);self.assertEqual(Vente.objects.get().montant_total,Decimal('1.00'))
        self.assertEqual(self.sell(h,0.5).status_code,400)
    def test_backdated_postharvest_sale_cannot_break_future_prefix(self):
        h=self.harvest();self.assertEqual(self.sell(h,'90.000000').status_code,201)
        at=(timezone.now()-timezone.timedelta(minutes=10)).isoformat();self.assertEqual(self.sell(h,'20.000000',occurred_at=at).status_code,409);self.assertEqual(Vente.objects.count(),1)
    def test_secondary_measure_omission_never_turns_unknown_into_zero(self):
        h=self.harvest();self.assertEqual(self.sell(h,'30.000000').status_code,201)
        self.assertIsNone(balance(h)['available_count']);self.assertEqual(self.sell(h,'70.000000').status_code,201);self.assertIsNone(balance(h)['available_count']);self.assertEqual(balance(h)['available_mass_kg'],'0.000000')

    def test_foreign_sale_customer_and_invoice_payment_are_masked(self):
        h=self.harvest();other=User.objects.create_user(username='foreign-commerce');client=Client.objects.create(exploitation=other.exploitation,nom='Client étranger')
        self.assertEqual(self.sell(h,price={'client':client.pk,'unit_price':'2.50'}).status_code,404);self.assertFalse(Vente.objects.exists())
        r=self.sell(h);self.assertEqual(r.status_code,201,r.data);sale=Vente.objects.get()
        from pisciculture.models import Activity
        Activity.objects.create(exploitation=other.exploitation,enabled=True)
        self.api.force_authenticate(other)
        self.assertEqual(self.post('commerce-payments/',sale=sale.pk,amount='1.00',occurred_at=timezone.now().isoformat()).status_code,404)
        self.assertEqual(self.api.get(PREFIX+'commerce-invoices/'+str(sale.pk)+'/').status_code,404)
    def test_sale_and_loss_permissions_checked_separately(self):
        h=self.harvest();data=self.sale_body(h)
        user=User.objects.create_user(username='commerce-readonly',exploitation=self.farm);member=ExploitationMembership.objects.get(user=user)
        MembershipPermission.objects.create(membership=member,code='aquatic.read');self.api.force_authenticate(user)
        self.assertEqual(self.post('harvest-sales/',**data).status_code,403);self.assertEqual(self.loss_post(h).status_code,403)
        self.assertFalse(Vente.objects.exists());self.assertEqual(balance(h)['available_quantity'],'100.000000')

    def test_existing_classic_payment_accepts_same_shared_sale_once(self):
        h=self.harvest();self.sell(h);sale=Vente.objects.get()
        employee=User.objects.create_user(username='classic-shared-cash',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=employee)
        MembershipPermission.objects.create(membership=member,code='cash.create')
        self.api.force_authenticate(employee)
        r=self.api.post('/api/payments/create/',{'client':self.customer.pk,'vente':sale.pk,'montant':'20.00'},format='json')
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(Payment.objects.count(),1);self.assertEqual(Lettrage.objects.count(),1);self.assertEqual(sale.reste_a_payer,Decimal('55'))

class CommerceConcurrency(CommerceFixture,TransactionTestCase):
    @skipUnless(connection.vendor=='postgresql','PostgreSQL required')
    def test_concurrent_sales_cannot_overdraw(self):
        h=self.harvest();data=self.sale_body(h,'70.000000');barrier=Barrier(2)
        def run(_):
            close_old_connections();api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait()
            try:return api.post(PREFIX+'harvest-sales/',{'schema_version':1,'operation_uuid':str(uuid.uuid4()),**data},format='json').status_code
            finally:close_old_connections()
        with ThreadPoolExecutor(2) as pool:codes=list(pool.map(run,range(2)))
        self.assertEqual(sorted(codes),[201,409]);self.assertEqual(Vente.objects.count(),1);self.assertEqual(balance(h)['available_quantity'],'30.000000')

    @skipUnless(connection.vendor=='postgresql','PostgreSQL required')
    def test_same_sale_uuid_concurrent_produces_one_invoice(self):
        h=self.harvest();data={'schema_version':1,'operation_uuid':str(uuid.uuid4()),**self.sale_body(h)};barrier=Barrier(2)
        def run(_):
            close_old_connections();api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait()
            try:
                r=api.post(PREFIX+'harvest-sales/',data,format='json');return r.status_code,r.data
            finally:close_old_connections()
        with ThreadPoolExecutor(2) as pool:rows=list(pool.map(run,range(2)))
        self.assertEqual([r[0] for r in rows],[201,201]);self.assertEqual(rows[0][1],rows[1][1]);self.assertEqual(Vente.objects.count(),1)
    @skipUnless(connection.vendor=='postgresql','PostgreSQL required')
    def test_concurrent_payments_never_overpay_same_invoice(self):
        h=self.harvest();self.sell(h);sale=Vente.objects.get();barrier=Barrier(2)
        def run(_):
            close_old_connections();api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait()
            try:return api.post(PREFIX+'commerce-payments/',{'schema_version':1,'operation_uuid':str(uuid.uuid4()),'sale':sale.pk,'amount':'50.00','occurred_at':timezone.now().isoformat()},format='json').status_code
            finally:close_old_connections()
        with ThreadPoolExecutor(2) as pool:codes=list(pool.map(run,range(2)))
        self.assertEqual(sorted(codes),[201,409]);self.assertEqual(Payment.objects.count(),1);self.assertEqual(sale.reste_a_payer,Decimal('25'))
