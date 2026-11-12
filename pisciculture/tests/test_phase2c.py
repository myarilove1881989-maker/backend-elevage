import uuid
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.test import TestCase,TransactionTestCase
from django.db import connection,transaction,DatabaseError,close_old_connections
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User,Lot,CategorieDepense,Depense,ConsommationAliment,ExploitationMembership,MembershipPermission
from core.supply_models import SupplyProduct,SupplyOperation,SupplyReceipt,SupplyMovement,SupplyCategoryBinding
from core.supply_services import stock,execute as supply_execute
from core.feed_stock import snapshot
from core.audit import ActorContext,audit_scope
from pisciculture.tests.test_phase2b import StockFixture
from pisciculture.tests.test_phase1 import PREFIX
from pisciculture.models import FeedDistribution,FeedPurchase,Command,Observation
from pisciculture.feeding import balance
from core.permission_catalog import AQUATIC_PERMISSIONS

class FeedFixture(StockFixture):
    def setUp(self):
        super().setUp()
        self.product=SupplyProduct.objects.create(exploitation=self.farm,kind='FEED',name='Aliment test')
        self.category=CategorieDepense.objects.create(exploitation=self.farm,nom='Alimentation')
        SupplyCategoryBinding.objects.create(category=self.category,purpose='FEED')
        self.purchase_at=(timezone.now()-timezone.timedelta(hours=1)).isoformat()
    def buy(self,quantity='100',amount='1200.00',**extra):
        d=dict(product=str(self.product.pk),category=self.category.pk,quantity_kg=quantity,
            amount=amount,unit='kg',occurred_at=self.purchase_at)
        d.update(extra);return self.post('feed-purchases/',**d)
    def distribute(self,quantity='15',**extra):
        d=dict(product=str(self.product.pk),installation=str(self.site.pk),quantity_kg=quantity,
            unit='kg',occurred_at=timezone.now().isoformat())
        d.update(extra);return self.post('feed-distributions/',**d)
    def reserve(self,source,target,quantity='10',**extra):
        return self.post('feed-reserves/',product=str(self.product.pk),source=source,target=target,
            quantity_kg=quantity,unit='kg',occurred_at=extra.pop('occurred_at',timezone.now().isoformat()),reason='Réserve explicitement choisie',**extra)
    def lot(self):
        return Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Lot existant',
            type_production='CHAIR',date_debut=timezone.localdate())

class FeedingTests(FeedFixture,TestCase):
    def test_date_only_preserves_business_date_without_known_hour(self):
        day=(timezone.now()-timezone.timedelta(days=1)).date()
        at=day.isoformat()+'T00:00:00+02:00'
        r=self.buy(occurred_at=at,time_precision='DATE',business_date=day.isoformat())
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['time_precision'],'DATE');self.assertEqual(r.data['business_date'],day.isoformat())
        self.assertEqual(Depense.objects.get(pk=r.data['expense_id']).date,day)
        self.create_cohort('OLD',50,site=self.other_site,started_at=(timezone.now()-timezone.timedelta(days=3)).isoformat())
        today=timezone.now().date().isoformat()
        r=self.distribute('1',installation=str(self.other_site.pk),occurred_at=today+'T00:00:00+00:00',time_precision='DATE',business_date=today)
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['business_date'],today)
        summary=self.api.get(PREFIX+'feed-summary/').data
        self.assertEqual(summary['by_day'],[{'date':today,'quantity_kg':'1.000'}])
        self.assertEqual(self.buy(time_precision='DATE').status_code,400)
        self.assertEqual(self.buy(time_precision='DATETIME',business_date=today).status_code,400)
        self.assertEqual(self.buy(occurred_at=today+'T12:00:00Z',time_precision='DATE',business_date=today).status_code,400)

    def test_purchase_100_then_15_then_20_shared_stock_and_single_expense(self):
        bought=self.buy();self.assertEqual(bought.status_code,201,bought.data)
        self.assertEqual(balance(self.product),100);self.assertEqual(Depense.objects.count(),1)
        expense=Depense.objects.get();self.assertIsNone(expense.lot_id);self.assertEqual(expense.shared_exploitation_id,self.farm.pk)
        self.assertEqual(Lot.objects.count(),0)
        self.assertEqual(self.distribute().status_code,201)
        self.assertEqual(balance(self.product),85)
        self.assertEqual(self.distribute('20').status_code,201)
        self.assertEqual(balance(self.product),65)
        self.assertEqual(stock(self.owner,self.farm.pk,self.product.pk)['physical'],65)
        self.assertEqual(Depense.objects.count(),1);self.assertEqual(SupplyReceipt.objects.count(),1)
        self.assertEqual(FeedDistribution.objects.count(),2)
        self.assertEqual(ConsommationAliment.objects.count(),0)

    def test_leftovers_null_zero_and_partial_summary(self):
        self.assertEqual(self.buy().status_code,201)
        a=self.distribute('10');self.assertEqual(a.status_code,201,a.data)
        self.assertIsNone(a.data['leftovers_kg']);self.assertIsNone(a.data['estimated_consumed_kg'])
        z=self.distribute('10',leftovers_kg='0');self.assertEqual(z.data['estimated_consumed_kg'],'10.000')
        r=self.distribute('10',leftovers_kg='2.5');self.assertEqual(r.data['estimated_consumed_kg'],'7.500')
        self.assertEqual(balance(self.product),70)
        s=self.api.get(PREFIX+'feed-summary/').data
        self.assertEqual(Decimal(s['distributed_kg']),30)
        self.assertIsNone(s['estimated_consumed_kg']);self.assertEqual(s['consumption_status'],'PARTIAL')
        self.assertEqual(Decimal(s['known_consumed_kg']),Decimal('17.5'))
        self.assertEqual(self.distribute('1',leftovers_kg='2').status_code,400)
        self.assertEqual(balance(self.product),70)

    def test_basin_multicohort_never_assigns_food_arbitrarily(self):
        self.buy()
        r=self.distribute();self.assertEqual(r.status_code,201,r.data);self.assertIsNone(r.data['cohort'])
        r=self.distribute('10',cohort=str(self.a.pk));self.assertEqual(r.status_code,201,r.data)
        s=self.api.get(PREFIX+'feed-summary/',{'cohort':str(self.a.pk)}).data
        self.assertEqual(Decimal(s['distributed_kg']),10)
        self.assertEqual(Decimal(s['unassigned_distributed_kg']),15)
        rows=self.api.get(PREFIX+'feed-distributions/',{'cohort':str(self.b.pk)}).data['results']
        self.assertEqual(rows,[])

    def test_insufficient_and_exact_validation_and_archived_product(self):
        self.buy()
        self.assertEqual(self.distribute('101').status_code,409)
        for quantity in ['0','-1','NaN','Infinity','0.0001',15.0,True]:
            self.assertEqual(self.distribute(quantity).status_code,400,quantity)
        self.assertEqual(self.distribute(unit='sac').status_code,400)
        self.product.archived=True;self.product.save()
        self.assertEqual(self.distribute().status_code,404)
        self.assertEqual(balance(self.product),100);self.assertEqual(Depense.objects.count(),1)

    def test_uuid_replays_and_cross_shared_ledger_collision(self):
        body=dict(operation_uuid=str(uuid.uuid4()),schema_version=1,product=str(self.product.pk),category=self.category.pk,
            quantity_kg='100',amount='1200.00',unit='kg',occurred_at=self.purchase_at)
        first=self.api.post(PREFIX+'feed-purchases/',body,format='json')
        second=self.api.post(PREFIX+'feed-purchases/',body,format='json')
        self.assertEqual(first.status_code,201,first.data);self.assertEqual(first.data,second.data)
        self.assertEqual(second['Idempotent-Replay'],'true');self.assertEqual(Depense.objects.count(),1)
        body['amount']='1300.00';self.assertEqual(self.api.post(PREFIX+'feed-purchases/',body,format='json').status_code,409)
        d=dict(operation_uuid=str(uuid.uuid4()),schema_version=1,product=str(self.product.pk),installation=str(self.site.pk),
            quantity_kg='15',unit='kg',occurred_at=timezone.now().isoformat())
        first=self.api.post(PREFIX+'feed-distributions/',d,format='json');second=self.api.post(PREFIX+'feed-distributions/',d,format='json')
        self.assertEqual(first.status_code,201,first.data);self.assertEqual(first.data,second.data)
        self.assertEqual(balance(self.product),85)
        d['operation_uuid']=body['operation_uuid']
        self.assertEqual(self.api.post(PREFIX+'feed-distributions/',d,format='json').status_code,409)
        self.assertEqual(balance(self.product),85)

    def test_terrestrial_reserves_protected_explicit_release_to_common(self):
        lot=self.lot()
        op=supply_execute(self.owner,self.farm.pk,uuid.uuid4(),'FEED_RECEIPT',
            {'product':str(self.product.pk),'species':self.species.pk,'lot':lot.pk,'kg':'100'})
        self.assertEqual(self.distribute().status_code,409)
        r=self.reserve({'species':self.species.pk,'lot':lot.pk},{},'40')
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(balance(self.product),40);self.assertEqual(balance(self.product,self.species,lot),60)
        self.assertEqual(self.distribute('15').status_code,201)
        self.assertEqual(balance(self.product),25)
        self.assertEqual(stock(self.owner,self.farm.pk,self.product.pk)['physical'],85)
        self.assertEqual(Depense.objects.count(),0)

    def test_common_stock_can_be_reserved_then_used_by_existing_terrestrial_service(self):
        self.buy();lot=self.lot()
        self.assertEqual(self.reserve({}, {'species':self.species.pk},'25').status_code,201)
        snap=snapshot(self.farm.pk)
        positions=[p for item in snap['products'] if item['id']==str(self.product.pk) for p in item['positions']]
        self.assertTrue(all(p['species_id'] is not None for p in positions))
        self.assertEqual(sum(Decimal(p['kg']) for p in positions),25)
        r=self.api.post('/api/alimentation/distributions/',{'stock_version':1,'operation_uuid':str(uuid.uuid4()),
            'lot':lot.pk,'species':self.species.pk,'product':str(self.product.pk),'quantite_kg':'10'},format='json',
            HTTP_ACCEPT='application/json; elevage-contract=2')
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(stock(self.owner,self.farm.pk,self.product.pk)['physical'],90)
        self.assertEqual(balance(self.product),75)
        self.assertEqual(Depense.objects.count(),1)

    def test_transferred_and_closed_cohorts_and_historical_presence(self):
        self.buy();self.assertEqual(self.transfer(600).status_code,201)
        self.assertEqual(self.distribute(cohort=str(self.a.pk)).status_code,409)
        self.a.status='CLOSED';self.a.save()
        self.assertEqual(self.distribute(installation=str(self.other_site.pk),cohort=str(self.a.pk)).status_code,404)
        self.a.status='OPEN';self.a.save()
        past=(timezone.now()-timezone.timedelta(minutes=30)).isoformat()
        self.assertEqual(self.distribute(cohort=str(self.a.pk),occurred_at=past).status_code,201)
        self.assertEqual(balance(self.product),85)

    def test_stock_date_prefix_and_rollback(self):
        self.buy()
        before=Command.objects.count()
        earlier=(timezone.now()-timezone.timedelta(hours=1,minutes=10)).isoformat()
        r=self.distribute(occurred_at=earlier);self.assertEqual(r.status_code,409,r.data)
        self.assertEqual(r.data['error']['code'],'FEED_TIMELINE_CONFLICT')
        self.assertEqual(balance(self.product),100);self.assertEqual(Command.objects.count(),before)
        with patch('pisciculture.services.Command.objects.create',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.distribute()
        self.assertEqual(balance(self.product),100);self.assertEqual(FeedDistribution.objects.count(),0)

    def test_foreign_references_permissions_revocation_and_owner_reserves(self):
        self.buy();user=User.objects.create_user(username='feed-operator',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=user)
        self.api.force_authenticate(user)
        self.assertEqual(self.distribute().status_code,403)
        for code in ['aquatic.read','aquatic.feed.distribute']:
            MembershipPermission.objects.create(membership=member,code=code)
        self.assertEqual(self.distribute('1').status_code,201)
        self.assertEqual(self.reserve({}, {'species':self.species.pk}).status_code,403)
        self.assertEqual(self.buy().status_code,403)
        MembershipPermission.objects.filter(membership=member,code='aquatic.feed.distribute').delete()
        self.assertEqual(self.distribute().status_code,403)
        member.is_active=False;member.save()
        self.assertEqual(self.api.get(PREFIX+'feed-products/').status_code,403)
        other=User.objects.create_user(username='other-feed')
        from pisciculture.models import Activity
        Activity.objects.create(exploitation=other.exploitation,enabled=True)
        self.api.force_authenticate(other)
        self.assertEqual(self.distribute().status_code,404)
        self.assertEqual(self.buy().status_code,404)
        self.assertEqual(self.api.get(PREFIX+'feed-products/').data['products'],[])

    def test_product_creation_reuses_catalog_and_purchase_failure_is_atomic(self):
        r=self.post('feed-purchases/',new_product={'id':str(uuid.uuid4()),'name':'Nouvel aliment'},new_category='Nourriture poissons',
            quantity_kg='100',amount='1000.00',unit='kg',occurred_at=self.purchase_at)
        self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(SupplyProduct.objects.count(),2);self.assertEqual(FeedPurchase.objects.count(),1)
        self.assertEqual(Depense.objects.count(),1)
        duplicate=self.post('feed-purchases/',new_product={'id':str(uuid.uuid4()),'name':'Nouvel aliment'},category=self.category.pk,
            quantity_kg='1',amount='1.00',unit='kg',occurred_at=self.purchase_at)
        self.assertEqual(duplicate.status_code,409);self.assertEqual(SupplyProduct.objects.count(),2)
        before=SupplyOperation.objects.count()
        r=self.buy(amount='-1');self.assertEqual(r.status_code,400)
        self.assertEqual(SupplyOperation.objects.count(),before)

    def test_ratio_requires_scope_policy_stable_counts_and_never_fcr(self):
        self.buy()
        first=self.distribute('1');self.assertEqual(first.data['analysis']['status'],'INSUFFICIENT_DATA')
        rule=self.post('observation-rules/',parameter='weight_age_days',scope='INSTALLATION',
            installation=str(self.site.pk),upper='1',valid_from=self.initial)
        self.assertEqual(rule.status_code,201,rule.data)
        at=(timezone.now()-timezone.timedelta(minutes=10)).isoformat()
        w=self.post('weights/',installation=str(self.site.pk),occurred_at=at,count_reference_at=at,direct_mean_g='250')
        self.assertEqual(w.status_code,201,w.data)
        r=self.distribute('15',leftovers_kg='0')
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['analysis']['status'],'INDICATIVE')
        self.assertEqual(r.data['analysis']['biomass_kg']['decimal'],'250')
        self.assertEqual(r.data['analysis']['feed_percent']['decimal'],'6')
        self.assertIsNone(r.data['analysis']['fcr'])
        self.loss()
        r=self.distribute('1');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['analysis']['status'],'INSUFFICIENT_DATA')
        self.assertEqual(r.data['analysis']['reason'],'UNRECONCILED')
        self.assertIsNone(r.data['analysis']['fcr'])

    def test_histories_filters_summary_no_double_expense_and_readonly(self):
        self.buy();self.distribute('15');self.distribute('20',cohort=str(self.a.pk),leftovers_kg='0')
        rows=self.api.get(PREFIX+'feed-distributions/',{'limit':1}).data
        self.assertIsNotNone(rows['next_cursor'])
        second=self.api.get(PREFIX+'feed-distributions/',{'limit':1,'cursor':rows['next_cursor']}).data
        self.assertNotEqual(rows['results'][0]['id'],second['results'][0]['id'])
        wrong=self.api.get(PREFIX+'feed-distributions/',{'limit':1,'cohort':str(self.a.pk),'cursor':rows['next_cursor']})
        self.assertEqual(wrong.status_code,400)
        summary=self.api.get(PREFIX+'feed-summary/').data
        self.assertEqual(Decimal(summary['distributed_kg']),35)
        self.assertEqual(Decimal(summary['common_stock_kg']),65)
        self.assertEqual(Decimal(summary['by_product'][0]['quantity_kg']),35)
        self.assertEqual(self.api.get(PREFIX+'feed-stock-history/').status_code,200)
        self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(self.api.delete(PREFIX+'feed-distributions/').status_code,405)

    def test_dashboard_species_filter_never_attributes_global_feed(self):
        self.buy();self.distribute()
        response=self.api.get(PREFIX+'dashboard/',{'species':self.species.pk})
        self.assertEqual(response.status_code,200,response.data)
        self.assertNotIn('feeding_overview',response.data)
        self.assertIsNone(response.data['metrics']['feeding']['value'])
        self.assertNotEqual(response.data['metrics']['feeding']['quality'],'RECORDED')
        self.assertEqual(response.data['feeding_unavailable_reason'],'SPECIES_SCOPE_NOT_ATTRIBUTABLE')

    def test_aquatic_expenses_not_in_legacy_lists_or_terrestrial_kpis_and_no_legacy_mutation(self):
        before=self.api.get('/api/dashboard/').data
        bought=self.buy();self.assertEqual(bought.status_code,201,bought.data)
        self.distribute()
        self.assertEqual(self.api.get('/api/dashboard/').data,before)
        expenses=self.api.get('/api/depenses/');self.assertEqual(expenses.status_code,200,expenses.data)
        self.assertEqual(expenses.data,[])
        expense=Depense.objects.get()
        r=self.api.delete('/api/depenses/'+str(expense.pk)+'/')
        self.assertIn(r.status_code,[404,405])
        self.assertEqual(Depense.objects.count(),1)
        from core.serializers import ConsommationAlimentSerializer
        from types import SimpleNamespace
        serializer=ConsommationAlimentSerializer(data={'lot':self.lot().pk,'aliment':'Test','quantite_kg':'1','depense':expense.pk},context={'request':SimpleNamespace(user=self.owner)})
        self.assertFalse(serializer.is_valid());self.assertIn('depense',serializer.errors)

    def test_facts_and_common_pool_validation(self):
        self.buy();self.distribute()
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):FeedDistribution.objects.all().update(note='rewrite')
        if connection.vendor=='postgresql':
            with self.assertRaises(DatabaseError),transaction.atomic():
                with connection.cursor() as c:c.execute('UPDATE pisciculture_feeddistribution SET quantity_kg=1')
        op=SupplyOperation.objects.first()
        vaccine=SupplyProduct.objects.create(exploitation=self.farm,kind='VACCINE',name='Test vaccin')
        with self.assertRaises(ValidationError):
            SupplyMovement.objects.create(operation=op,line=99,product=vaccine,species=None,kind='ENTRY',quantity=1)
        self.assertEqual(self.api.delete(PREFIX+'feed-purchases/').status_code,405)

    def test_generic_reverse_cannot_return_food_or_reverse_expense_implicitly(self):
        self.buy();self.distribute()
        op=SupplyOperation.objects.get(kind='AQUA_FEED_DISTRIB')
        with self.assertRaises(Exception):
            supply_execute(self.owner,self.farm.pk,uuid.uuid4(),'REVERSE',{'original':str(op.operation_uuid)},reason='not a leftovers return')
        self.assertEqual(balance(self.product),85);self.assertEqual(Depense.objects.count(),1)

@skipUnless(connection.vendor=='postgresql','Real concurrent PostgreSQL required')
class FeedingConcurrencyTests(FeedFixture,TransactionTestCase):
    def send_pair(self,bodies):
        barrier=Barrier(2)
        def send(body):
            close_old_connections()
            try:
                api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait(timeout=15)
                r=api.post(PREFIX+'feed-distributions/',body,format='json');return r.status_code,r.data
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(send,bodies))
    def payload(self,quantity='60'):
        return dict(operation_uuid=str(uuid.uuid4()),schema_version=1,product=str(self.product.pk),
            installation=str(self.site.pk),quantity_kg=quantity,unit='kg',occurred_at=timezone.now().isoformat())
    def test_two_distributions_cannot_spend_same_stock(self):
        self.buy();responses=self.send_pair([self.payload(),self.payload()])
        self.assertEqual(sorted(x[0] for x in responses),[201,409])
        self.assertEqual(balance(self.product),40);self.assertEqual(Depense.objects.count(),1)
    def test_same_uuid_is_debited_once(self):
        self.buy();body=self.payload('15');responses=self.send_pair([body,body])
        self.assertEqual(responses[0],responses[1]);self.assertEqual(responses[0][0],201)
        self.assertEqual(balance(self.product),85);self.assertEqual(FeedDistribution.objects.count(),1)
