import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest.mock import patch
from unittest import skipUnless
from django.db import connection,transaction,DatabaseError,close_old_connections
from django.test import TestCase,TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User,Espece,ExploitationMembership,MembershipPermission,Lot,Depense,Payment
from pisciculture.models import Activity,Installation,AquaticSpeciesProfile,Cohort,Position,StockFact,Command,Observation
from pisciculture.tests.test_phase1 import PREFIX
from core.permission_catalog import AQUATIC_PERMISSIONS

class StockFixture:
    def setUp(self):
        self.owner=User.objects.create_user(username='stock-owner',password='synthetic-local-only')
        self.farm=self.owner.exploitation
        self.api=APIClient();self.api.force_authenticate(self.owner)
        Activity.objects.create(exploitation=self.farm,enabled=True)
        self.species=Espece.objects.create(exploitation=self.farm,nom='Tilapia')
        AquaticSpeciesProfile.objects.create(exploitation=self.farm,species=self.species)
        self.site=Installation.objects.create(exploitation=self.farm,code='N',name='Nord',type='BASSIN')
        self.other_site=Installation.objects.create(exploitation=self.farm,code='S',name='Sud',type='BASSIN')
        self.initial=(timezone.now()-timezone.timedelta(hours=2)).isoformat()
        self.a=self.create_cohort('A',600)
        self.b=self.create_cohort('B',400)

    def create_cohort(self,code,count,site=None,started_at=None):
        r=self.post('cohorts/',id=str(uuid.uuid4()),code=code,name=code,species=self.species.pk,
            origin={'type':'OTHER'},started_at=started_at or self.initial,initial_count=count,
            allocations=[{'installation':str((site or self.site).pk),'count':count}],reason='Initial')
        self.assertEqual(r.status_code,201,r.data)
        return Cohort.objects.get(pk=r.data['id'])

    def post(self,path,**data):
        return self.api.post(PREFIX+path,{'operation_uuid':str(uuid.uuid4()),'schema_version':1,**data},format='json')

    def state(self,**filters):
        r=self.api.get(PREFIX+'stock-state/',filters);self.assertEqual(r.status_code,200,r.data);return r.data

    def stock(self,path,**data):
        return self.post(path,occurred_at=data.pop('occurred_at',timezone.now().isoformat()),
            reason=data.pop('reason','Constat justifié'),**data)

    def loss(self,n=10,**data):
        return self.stock('mortalities/',installation=str(self.site.pk),quantity=n,**data)

    def reconcile(self,loss,allocations,**extra):
        return self.stock('loss-reconciliations/',pending=loss.data['fact']['id'],
            allocations=[{'cohort':str(c.pk),'quantity':n} for c,n in allocations],**extra)

    def inventory(self,n,**extra):
        return self.stock('inventories/',installation=str(self.site.pk),observed_count=n,
            expected_revision=extra.pop('expected_revision',self.state()['business_revision']),**extra)

    def assign(self,credit,n,cohort=None,**extra):
        return self.stock('inventory-credits/assign/',credit=credit.data['fact']['id'],
            quantity=n,cohort=str((cohort or self.a).pk),**extra)

    def cancel(self,fact,**extra):
        return self.stock('stock-cancellations/',target=fact.data['fact']['id'],**extra)

    def transfer_body(self,n=150,at=None):
        self.a.refresh_from_db()
        src=Position.objects.get(cohort=self.a,installation=self.site)
        dst=Position.objects.filter(cohort=self.a,installation=self.other_site).first()
        return dict(operation_uuid=str(uuid.uuid4()),schema_version=1,cohort=str(self.a.pk),
            source=str(self.site.pk),destination=str(self.other_site.pk),count=n,
            occurred_at=at or timezone.now().isoformat(),reason='Répartition',
            expected_versions={'cohort':self.a.version,'source_position':src.version,'destination_position':dst.version if dst else 0})

    def transfer(self,n=150,at=None):
        return self.api.post(PREFIX+'transfers/',self.transfer_body(n,at),format='json')

class StockTests(StockFixture,TestCase):
    def test_600_400_then_10_then_6_4_no_second_loss(self):
        loss=self.loss();self.assertEqual(loss.status_code,201,loss.data)
        state=self.state();self.assertEqual(state['live_count'],990);self.assertEqual(state['unassigned_losses'],10)
        self.assertEqual(sorted(x['gross_count'] for x in state['positions']),[400,600])
        r=self.reconcile(loss,[(self.a,6),(self.b,4)]);self.assertEqual(r.status_code,201,r.data)
        state=self.state();self.assertEqual(state['live_count'],990);self.assertEqual(state['unassigned_losses'],0)
        self.assertEqual(sorted(x['gross_count'] for x in state['positions']),[396,594])
        self.assertEqual(self.api.get(PREFIX+'dashboard/').data['metrics']['live_count']['value'],990)

    def test_partial_multiple_pending_and_reserves(self):
        first=self.loss(10);second=self.loss(20)
        self.assertEqual(self.reconcile(first,[(self.a,6)]).status_code,201)
        state=self.state();self.assertEqual(state['live_count'],970)
        self.assertEqual(state['unassigned_losses'],24)
        self.assertEqual(sorted(x['remaining'] for x in state['pending']),[4,20])
        self.assertEqual({x['reserved_count'] for x in state['positions']},{24})
        self.assertEqual(self.reconcile(second,[(self.b,20)]).status_code,201)
        self.assertEqual(self.reconcile(first,[(self.b,4)]).status_code,201)
        self.assertEqual(self.state()['live_count'],970)

    def test_known_loss_and_suspicion(self):
        r=self.loss(10,cohort=str(self.a.pk));self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(self.state()['live_count'],990)
        self.assertEqual(self.state()['unassigned_losses'],0)
        before=self.state()['positions']
        suspected=self.loss(90,suspected=True)
        self.assertEqual(suspected.status_code,201,suspected.data)
        self.assertEqual(self.state()['positions'],before)
        self.assertEqual(self.state()['live_count'],990)
        self.assertEqual(self.cancel(suspected).status_code,201)

    def test_transfer_pending_then_reconcile_no_loss_moves_with_fish(self):
        loss=self.loss()
        r=self.transfer();self.assertEqual(r.status_code,201,r.data)
        self.assertEqual([p['gross_count'] for p in r.data['positions']],[450,150])
        self.assertEqual(self.state()['live_count'],990)
        self.assertEqual(self.reconcile(loss,[(self.a,6),(self.b,4)]).status_code,201)
        positions=self.state()['positions']
        self.assertEqual(sorted(x['gross_count'] for x in positions),[150,396,444])
        self.assertEqual(self.state()['live_count'],990)

    def test_conservative_transfer_and_overdraw_refused(self):
        self.loss()
        self.assertEqual(self.transfer(595).status_code,409)
        self.assertEqual(self.transfer(601).status_code,409)
        self.assertEqual(self.state()['live_count'],990)
        self.assertEqual(self.loss(1001).status_code,409)
        self.assertEqual(self.state()['unassigned_losses'],10)

    def test_negative_inventory_does_not_recount_pending_deaths(self):
        loss=self.loss()
        equal=self.inventory(990);self.assertEqual(equal.status_code,201,equal.data)
        self.assertEqual(equal.data['fact']['quantity'],0)
        correction=self.inventory(985);self.assertEqual(correction.status_code,201,correction.data)
        self.assertEqual(correction.data['fact']['quantity'],5)
        self.assertEqual(correction.data['fact']['kind'],'INVENTORY')
        self.assertEqual(self.state()['unassigned_losses'],15)
        self.assertEqual(self.reconcile(loss,[(self.a,6),(self.b,4)]).status_code,201)
        self.assertEqual(self.reconcile(correction,[(self.a,5)]).status_code,201)
        self.assertEqual(self.state()['live_count'],985)

    def test_positive_credit_separate_untransferable_then_assignment(self):
        credit=self.inventory(1010);self.assertEqual(credit.status_code,201,credit.data)
        state=self.state();self.assertEqual(state['live_count'],1010);self.assertEqual(state['unassigned_credits'],10)
        self.assertEqual(sum(x['gross_count'] for x in state['positions']),1000)
        self.assertEqual(self.transfer(601).status_code,409)
        self.assertEqual(self.assign(credit,6).status_code,201)
        self.assertEqual(self.state()['live_count'],1010)
        self.assertEqual(self.state()['unassigned_credits'],4)
        self.assertEqual(self.assign(credit,4,self.b).status_code,201)
        self.assertEqual(self.state()['unassigned_credits'],0)
        self.assertEqual(sum(x['gross_count'] for x in self.state()['positions']),1010)

    def test_pending_credit_requires_explicit_resolution_before_other_inventory_change(self):
        credit=self.inventory(1010)
        self.assertEqual(self.inventory(1010).status_code,201)
        r=self.inventory(1005);self.assertEqual(r.status_code,409)
        self.assertEqual(r.data['error']['code'],'INVENTORY_CREDIT_PENDING')
        self.assertEqual(self.state()['live_count'],1010)

    def test_cancellations_are_compensations_and_dependency_aware(self):
        loss=self.loss()
        allocation=self.reconcile(loss,[(self.a,6)])
        self.assertEqual(self.cancel(loss).status_code,409)
        undo=self.cancel(allocation);self.assertEqual(undo.status_code,201,undo.data)
        self.assertEqual(self.state()['live_count'],990)
        self.assertEqual(self.state()['unassigned_losses'],10)
        self.assertEqual(self.cancel(loss).status_code,201)
        self.assertEqual(self.state()['live_count'],1000)
        self.assertEqual(self.cancel(loss).status_code,409)
        self.assertEqual(self.cancel(undo).status_code,400)
        self.assertEqual(StockFact.objects.filter(kind='CANCEL').count(),2)

    def test_cancel_assigned_loss_and_credit_dependencies(self):
        known=self.loss(10,cohort=str(self.a.pk))
        self.assertEqual(self.cancel(known).status_code,201)
        credit=self.inventory(1010);allocation=self.assign(credit,10)
        self.assertEqual(self.cancel(credit).status_code,409)
        self.assertEqual(self.cancel(allocation).status_code,201)
        self.assertEqual(self.cancel(credit).status_code,201)
        self.assertEqual(self.state()['live_count'],1000)

    def test_antidated_loss_invalidates_later_transfer_rolls_back(self):
        at=timezone.now()-timezone.timedelta(minutes=30)
        self.assertEqual(self.transfer(595,at.isoformat()).status_code,201)
        before=Command.objects.count()
        result=self.loss(10,occurred_at=(at-timezone.timedelta(minutes=5)).isoformat())
        self.assertEqual(result.status_code,409,result.data)
        self.assertEqual(result.data['error']['code'],'TIMELINE_CONFLICT')
        self.assertEqual(StockFact.objects.count(),0)
        self.assertEqual(Command.objects.count(),before)
        self.assertEqual(self.state()['live_count'],1000)

    def test_antidated_consistent_loss_and_historical_state(self):
        at=timezone.now()-timezone.timedelta(minutes=30)
        self.assertEqual(self.transfer(150,at.isoformat()).status_code,201)
        loss=self.loss(10,occurred_at=(at-timezone.timedelta(minutes=5)).isoformat())
        self.assertEqual(loss.status_code,201,loss.data)
        before=self.state(as_of=(at-timezone.timedelta(minutes=10)).isoformat())
        self.assertEqual(before['live_count'],1000)
        self.assertEqual(self.state()['live_count'],990)
        positions=self.api.get(PREFIX+'positions/',{'as_of':at.isoformat()})
        self.assertEqual(positions.status_code,200,positions.data)
        self.assertEqual(sum(x['gross_count'] for x in positions.data['results']),1000)

    def test_inventory_stale_revision_and_late_retroactive_change(self):
        stale=self.state()['business_revision'];self.loss(1)
        self.assertEqual(self.inventory(999,expected_revision=stale).status_code,409)
        at=timezone.now()-timezone.timedelta(minutes=5)
        self.assertEqual(self.inventory(1005).status_code,201)
        self.assertEqual(self.loss(1,occurred_at=at.isoformat()).status_code,409)
        self.assertEqual(self.state()['live_count'],1005)

    def test_eligibility_no_cohort_arriving_after_loss(self):
        loss=self.loss()
        newcomer=self.create_cohort('C',10,started_at=timezone.now().isoformat())
        r=self.reconcile(loss,[(newcomer,1)])
        self.assertEqual(r.status_code,409,r.data)
        self.assertEqual(r.data['error']['code'],'INELIGIBLE_COHORT')
        self.assertEqual(self.state()['unassigned_losses'],10)

    def test_antidated_new_cohort_cannot_rewrite_loss_eligibility(self):
        self.loss()
        before=Cohort.objects.count()
        r=self.post('cohorts/',id=str(uuid.uuid4()),code='LATE',name='Late',species=self.species.pk,
            origin={'type':'OTHER'},started_at=self.initial,initial_count=10,
            allocations=[{'installation':str(self.site.pk),'count':10}],reason='Antidatation')
        self.assertEqual(r.status_code,409,r.data)
        self.assertEqual(r.data['error']['code'],'TIMELINE_CONFLICT')
        self.assertEqual(Cohort.objects.count(),before)
        self.assertEqual(self.state()['live_count'],990)


    def test_filtered_dashboard_never_shows_other_cohort_as_this_cohort(self):
        loss=self.loss()
        filtered=self.api.get(PREFIX+'dashboard/',{'cohort':str(self.a.pk)}).data
        row=filtered['by_installation'][0]
        self.assertEqual((row['count'],row['lower'],row['upper']),(600,590,600))
        self.assertEqual(row['quality'],'UNRECONCILED')
        self.reconcile(loss,[(self.a,6),(self.b,4)])
        filtered=self.api.get(PREFIX+'dashboard/',{'cohort':str(self.a.pk)}).data
        self.assertEqual(filtered['by_installation'][0]['count'],594)
        self.assertEqual(filtered['metrics']['live_count']['value'],594)

    def test_compensations_remain_in_cohort_history(self):
        loss=self.loss();undo=self.cancel(loss)
        history=self.api.get(PREFIX+'stock-history/',{'cohort':str(self.a.pk)})
        self.assertEqual(history.status_code,200,history.data)
        self.assertEqual({x['kind'] for x in history.data['results']},{'MORTALITY','CANCEL'})

    def test_unknown_death_requires_credit_identification_first(self):
        credit=self.inventory(1010)
        r=self.loss(1)
        self.assertEqual(r.status_code,409,r.data)
        self.assertEqual(r.data['error']['code'],'UNASSIGNED_CREDIT_REQUIRES_ASSIGNMENT')
        self.assertEqual(self.state()['live_count'],1010)
        self.assertEqual(self.assign(credit,10).status_code,201)
        self.assertEqual(self.loss(1).status_code,201)
        self.assertEqual(self.state()['live_count'],1009)

    def test_credit_assignment_then_transfer_beyond_original_count(self):
        credit=self.inventory(1010)
        self.assertEqual(self.assign(credit,10).status_code,201)
        self.assertEqual(self.transfer(610).status_code,201)
        self.assertEqual(self.state()['live_count'],1010)

    def test_inactive_installation_and_closed_cohort_cannot_be_revived_by_cancellation(self):
        loss=self.loss(600,cohort=str(self.a.pk))
        self.a.refresh_from_db()
        self.a.status='CLOSED';self.a.save()
        self.assertEqual(self.cancel(loss).status_code,409)
        self.assertEqual(self.state()['live_count'],400)
        self.a.status='OPEN';self.a.save()
        self.site.status='INACTIVE';self.site.save()
        self.assertEqual(self.cancel(loss).status_code,409)
        self.assertEqual(self.state()['live_count'],400)

    def test_new_zero_cohort_cannot_close_while_eligible_for_pending_loss(self):
        loss=self.loss(10)
        self.assertEqual(self.loss(600,cohort=str(self.a.pk)).status_code,201)
        self.a.refresh_from_db()
        self.assertEqual(self.a.positions.get(installation=self.site).gross_count,0)
        r=self.api.patch(PREFIX+'cohorts/'+str(self.a.pk)+'/',
            {'operation_uuid':str(uuid.uuid4()),'schema_version':1,'status':'CLOSED'},format='json',HTTP_IF_MATCH=f'"{self.a.pk}:{self.a.version}"')
        self.assertEqual(r.status_code,409)
        self.assertEqual(self.state()['live_count'],390)

    def test_cancel_assigned_death_does_not_cancel_other_counted_deaths(self):
        known=self.loss(5,cohort=str(self.a.pk));unknown=self.loss(10)
        self.assertEqual(self.cancel(known).status_code,201)
        self.assertEqual(self.state()['live_count'],990)
        self.assertEqual(self.state()['unassigned_losses'],10)
        self.assertEqual(self.reconcile(unknown,[(self.a,10)]).status_code,201)
        self.assertEqual(self.state()['live_count'],990)

    def test_read_cannot_mutate_and_no_delete_of_facts(self):
        loss=self.loss()
        self.assertEqual(self.api.delete(PREFIX+'mortalities/').status_code,405)
        self.assertEqual(self.api.patch(PREFIX+'inventories/',{},format='json').status_code,405)
        before=StockFact.objects.count()
        self.api.get(PREFIX+'stock-state/');self.api.get(PREFIX+'stock-history/')
        self.assertEqual(StockFact.objects.count(),before)

    def test_owner_only_even_if_all_action_permissions_are_granted(self):
        user=User.objects.create_user(username='stock-operator',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=user)
        for code in AQUATIC_PERMISSIONS:MembershipPermission.objects.create(membership=member,code=code)
        loss=self.loss();self.api.force_authenticate(user)
        self.assertEqual(self.loss(1).status_code,201)
        for response in [self.reconcile(loss,[(self.a,1)]),self.inventory(999),self.cancel(loss)]:
            self.assertEqual(response.status_code,403,response.data)
            self.assertEqual(response.data['error']['code'],'OWNER_REQUIRED')
        MembershipPermission.objects.filter(membership=member,code='aquatic.losses.create').delete()
        self.assertEqual(self.loss(1).status_code,403)
        member.is_active=False;member.save()
        self.assertEqual(self.api.get(PREFIX+'stock-state/').status_code,403)

    def test_foreign_references_and_cursor_isolation(self):
        loss=self.loss()
        other=User.objects.create_user(username='foreign-stock');Activity.objects.create(exploitation=other.exploitation,enabled=True)
        self.api.force_authenticate(other)
        self.assertEqual(self.api.get(PREFIX+'stock-history/').data['results'],[])
        self.assertEqual(self.loss().status_code,404)
        self.assertEqual(self.reconcile(loss,[(self.a,1)]).status_code,404)
        self.assertEqual(self.cancel(loss).status_code,404)
        self.api.force_authenticate(self.owner);self.loss(1)
        first=self.api.get(PREFIX+'stock-history/',{'limit':1}).data
        second=self.api.get(PREFIX+'stock-history/',{'limit':1,'cursor':first['next_cursor']}).data
        self.assertNotEqual(first['results'][0]['id'],second['results'][0]['id'])
        self.assertEqual(self.api.get(PREFIX+'stock-history/',{'limit':1,'cohort':str(self.a.pk),'cursor':first['next_cursor']}).status_code,400)

    def test_uuid_replay_collision_and_no_double_deduction(self):
        body=dict(operation_uuid=str(uuid.uuid4()),schema_version=1,installation=str(self.site.pk),quantity=10,
            occurred_at=timezone.now().isoformat(),reason='Constat')
        first=self.api.post(PREFIX+'mortalities/',body,format='json')
        second=self.api.post(PREFIX+'mortalities/',body,format='json')
        self.assertEqual(first.status_code,201,first.data);self.assertEqual(first.data,second.data)
        self.assertEqual(second['Idempotent-Replay'],'true')
        body['quantity']=11;self.assertEqual(self.api.post(PREFIX+'mortalities/',body,format='json').status_code,409)
        self.assertEqual(self.state()['live_count'],990)

    def test_atomic_failure_and_facts_immutable(self):
        before=Command.objects.count()
        with patch('pisciculture.services.Command.objects.create',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.loss()
        self.assertEqual(StockFact.objects.count(),0);self.assertEqual(self.state()['live_count'],1000)
        self.assertEqual(Command.objects.count(),before)
        self.loss()
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):StockFact.objects.all().update(reason='rewrite')
        with self.assertRaises(DatabaseError),transaction.atomic():
            with connection.cursor() as c:c.execute('UPDATE pisciculture_stockfact SET quantity=1')

    def test_biomass_snapshots_preserved_current_uncertainty_flagged(self):
        first=self.post('weights/',cohort=str(self.a.pk),occurred_at=timezone.now().isoformat(),
            count_reference_at=self.initial,direct_mean_g='250')
        self.assertEqual(first.status_code,201,first.data)
        loss=self.loss()
        history=self.api.get(PREFIX+'weights/').data['results'][0]
        self.assertEqual(history['result'],first.data['result'])
        self.assertEqual(history['current_effectif_quality'],'UNRECONCILED')
        new=self.post('weights/',cohort=str(self.a.pk),occurred_at=timezone.now().isoformat(),
            count_reference_at=self.initial,direct_mean_g='250')
        self.assertEqual(new.status_code,201,new.data)
        self.assertEqual(new.data['result']['count_reference']['quality'],'EXACT')
        at=timezone.now().isoformat()
        measured=self.post('weights/',cohort=str(self.a.pk),occurred_at=at,count_reference_at=at,direct_mean_g='250')
        self.assertEqual(measured.status_code,201,measured.data);self.assertIsNone(measured.data['result']['biomass_kg'])
        self.assertEqual(self.reconcile(loss,[(self.a,6),(self.b,4)]).status_code,201)
        at=timezone.now().isoformat()
        final=self.post('weights/',cohort=str(self.a.pk),occurred_at=at,count_reference_at=at,direct_mean_g='250')
        self.assertEqual(final.status_code,201,final.data)
        self.assertEqual(final.data['result']['biomass_kg']['decimal'],'148.5')

    def test_no_financial_terrestrial_effect_and_filtered_uncertainty(self):
        before=self.api.get('/api/dashboard/').data
        self.loss();self.inventory(995)
        self.assertEqual(self.api.get('/api/dashboard/').data,before)
        for model in [Lot,Depense,Payment]:self.assertEqual(model.objects.count(),0)
        filtered=self.api.get(PREFIX+'dashboard/',{'cohort':str(self.a.pk)}).data['metrics']['live_count']
        self.assertEqual(filtered['quality'],'UNRECONCILED')
        self.assertEqual((filtered['lower'],filtered['upper']),(590,600))

    def test_negative_fields_and_sensitive_closure(self):
        for quantity in [0,-1,True,1.5,'10']:
            self.assertEqual(self.loss(quantity).status_code,400)
        self.assertEqual(self.loss(1,occurred_at='2026-11-12').status_code,400)
        loss=self.loss()
        self.assertEqual(self.reconcile(loss,[(self.a,11)]).status_code,409)
        self.assertEqual(self.reconcile(loss,[(self.a,1),(self.a,1)]).status_code,400)
        self.assertEqual(self.cancel(loss,occurred_at=self.initial).status_code,409)
        self.a.refresh_from_db()
        r=self.api.patch(PREFIX+'cohorts/'+str(self.a.pk)+'/',
            {'operation_uuid':str(uuid.uuid4()),'schema_version':1,'status':'CLOSED'},format='json',HTTP_IF_MATCH=f'"{self.a.pk}:{self.a.version}"')
        self.assertEqual(r.status_code,409)

    def test_device_proof_required(self):
        user=User.objects.create_user(username='secured-stock',exploitation=self.farm)
        member=ExploitationMembership.objects.get(user=user)
        MembershipPermission.objects.create(membership=member,code='aquatic.losses.create')
        self.farm.offline_policy_enabled=True;self.farm.save();self.api.force_authenticate(user)
        r=self.loss();self.assertEqual(r.status_code,403,r.data)
        self.assertEqual(r.data['error']['code'],'DEVICE_PROOF_REQUIRED')

@skipUnless(connection.vendor=='postgresql','Real concurrency requires PostgreSQL')
class StockConcurrencyTests(StockFixture,TransactionTestCase):
    def concurrent(self,path,bodies):
        barrier=Barrier(2)
        def send(body):
            close_old_connections()
            try:
                client=APIClient();client.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait(timeout=15)
                r=client.post(PREFIX+path,body,format='json');return r.status_code,r.data
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(send,bodies))
    def test_same_loss_uuid_once(self):
        body=dict(operation_uuid=str(uuid.uuid4()),schema_version=1,installation=str(self.site.pk),quantity=10,
            occurred_at=timezone.now().isoformat(),reason='Constat')
        responses=self.concurrent('mortalities/',[body,body])
        self.assertEqual(responses[0],responses[1]);self.assertEqual(responses[0][0],201)
        self.assertEqual(StockFact.objects.count(),1);self.assertEqual(self.state()['live_count'],990)
    def test_two_reconciliations_cannot_consume_same_pending(self):
        loss=self.loss()
        body=dict(schema_version=1,pending=loss.data['fact']['id'],allocations=[{'cohort':str(self.a.pk),'quantity':10}],
            occurred_at=timezone.now().isoformat(),reason='Attribution')
        bodies=[dict(body,operation_uuid=str(uuid.uuid4())) for _ in range(2)]
        responses=self.concurrent('loss-reconciliations/',bodies)
        self.assertEqual(sorted(x[0] for x in responses),[201,409])
        self.assertEqual(self.state()['live_count'],990);self.assertEqual(self.state()['unassigned_losses'],0)

    def test_two_losses_cannot_exceed_available_living_fish(self):
        body=dict(schema_version=1,installation=str(self.site.pk),quantity=700,
            occurred_at=timezone.now().isoformat(),reason='Constat')
        responses=self.concurrent('mortalities/',[dict(body,operation_uuid=str(uuid.uuid4())) for _ in range(2)])
        self.assertEqual(sorted(x[0] for x in responses),[201,409])
        self.assertEqual(self.state()['live_count'],300)
    def test_credit_cannot_be_allocated_twice(self):
        credit=self.inventory(1010)
        body=dict(schema_version=1,credit=credit.data['fact']['id'],cohort=str(self.a.pk),quantity=8,
            occurred_at=timezone.now().isoformat(),reason='Affectation')
        responses=self.concurrent('inventory-credits/assign/',[dict(body,operation_uuid=str(uuid.uuid4())) for _ in range(2)])
        self.assertEqual(sorted(x[0] for x in responses),[201,409])
        self.assertEqual(self.state()['live_count'],1010)
        self.assertEqual(self.state()['unassigned_credits'],2)
