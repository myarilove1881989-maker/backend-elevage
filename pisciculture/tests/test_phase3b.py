import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.test import TestCase,TransactionTestCase
from django.db import connection,transaction,DatabaseError,close_old_connections
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User,ExploitationMembership,MembershipPermission,Depense,Mouvement
from core.supply_models import SupplyMovement
from pisciculture.models import BroodGroup,Reproduction,BreedingFact,Cohort,Position,Event,Command,CountLine
from pisciculture.tests.test_phase2b import StockFixture
from pisciculture.tests.test_phase1 import PREFIX

class BreedingFixture(StockFixture):
    def setUp(self):
        super().setUp()
        now=timezone.now()
        self.group_at=(now-timezone.timedelta(minutes=90)).isoformat()
        self.rep_at=(now-timezone.timedelta(minutes=80)).isoformat()
        self.hatch_at=(now-timezone.timedelta(minutes=60)).isoformat()
        r=self.post('brood-groups/',id=str(uuid.uuid4()),name='Géniteurs de test',species=self.species.pk,installation=str(self.site.pk),cohort=str(self.a.pk),occurred_at=self.group_at)
        self.assertEqual(r.status_code,201,r.data);self.group=BroodGroup.objects.get(pk=r.data['id'])
        r=self.post('reproductions/',id=str(uuid.uuid4()),group=str(self.group.pk),installation=str(self.site.pk),occurred_at=self.rep_at)
        self.assertEqual(r.status_code,201,r.data);self.rep=Reproduction.objects.get(pk=r.data['id'])
    def step(self,kind='HATCH',quantity=100,quality='COUNTED',**extra):
        data={'reproduction':str(self.rep.pk),'kind':kind,'installation':str(self.site.pk),'occurred_at':self.hatch_at,'quality':quality,'quantity':quantity}
        data.update(extra);return self.post('breeding-facts/',**data)
    def hatch(self,**extra):
        r=self.step(**extra);self.assertEqual(r.status_code,201,r.data);return BreedingFact.objects.get(pk=r.data['id'])
    def placement_body(self,hatch,n=40,**extra):
        identity=str(uuid.uuid4())
        body={'hatch':str(hatch.pk),'installation':str(self.site.pk),'occurred_at':timezone.now().isoformat(),
            'quantity':n,'actually_counted':True,'id':identity,'code':'ALV-'+identity[:12],'name':'Alevins de test','reason':'Mise en élevage comptée'}
        body.update(extra);return body
    def place(self,hatch,n=40,**extra):return self.post('fry-placements/',**self.placement_body(hatch,n,**extra))
    def count(self,hatch,total,**extra):return self.post('hatch-counts/',hatch=str(hatch.pk),installation=str(self.site.pk),occurred_at=extra.pop('occurred_at',timezone.now().isoformat()),quantity=total,actually_counted=True,reason='Comptage effectif de test',**extra)
    def void(self,row,**extra):return self.post('breeding-voids/',target=str(row.pk),occurred_at=extra.pop('occurred_at',timezone.now().isoformat()),reason='Annulation documentée',**extra)
    def availability(self,hatch):return self.api.get(PREFIX+'reproductions/'+str(self.rep.pk)+'/').data['hatches'][0]
    def live(self):return self.state()['live_count']

class BreedingTests(BreedingFixture,TestCase):
    def test_group_and_reproduction_never_add_or_reserve_fish(self):
        self.assertEqual(self.live(),1000);self.assertIsNone(self.group.males);self.assertIsNone(self.group.females)
        self.assertEqual(self.group.cohort_id,self.a.pk)
        self.assertEqual(sum(Position.objects.values_list('reserved_count',flat=True)),0)
        self.assertEqual(Cohort.objects.count(),2);self.assertEqual(Event.objects.count(),2)
        self.assertEqual(Depense.objects.count(),0);self.assertEqual(SupplyMovement.objects.count(),0);self.assertEqual(Mouvement.objects.count(),0)
    def test_unknown_origin_and_sex_zero_distinct_from_unknown(self):
        r=self.post('brood-groups/',id=str(uuid.uuid4()),name='Origine inconnue',species=self.species.pk,installation=str(self.site.pk),occurred_at=self.group_at,males=0,females=None)
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['males'],0);self.assertIsNone(r.data['females']);self.assertEqual(r.data['source_snapshot']['quality'],'UNLINKED')
        self.assertEqual(self.live(),1000)
    def test_brood_sex_species_and_presence_validation(self):
        body={'id':str(uuid.uuid4()),'name':'Invalid','species':self.species.pk,'installation':str(self.site.pk),'cohort':str(self.a.pk),'occurred_at':self.group_at}
        self.assertEqual(self.post('brood-groups/',**body,males=601).status_code,400)
        self.assertEqual(self.post('brood-groups/',**body,males=1.5).status_code,400)
        self.assertEqual(self.post('brood-groups/',**{**body,'installation':str(self.other_site.pk)}).status_code,400)
        self.assertEqual(self.post('reproductions/',id=str(uuid.uuid4()),group=str(self.group.pk),installation=str(self.site.pk),occurred_at=self.initial).status_code,400)
    def test_optional_stages_and_estimated_unknown_eggs_never_add_fry(self):
        spawn=self.step(kind='SPAWN',quantity=1000,quality='ESTIMATED');self.assertEqual(spawn.status_code,201,spawn.data)
        incubation=self.step(kind='INCUBATION',quantity=None,quality='UNKNOWN',parent=spawn.data['id']);self.assertEqual(incubation.status_code,201,incubation.data)
        hatch=self.step(quantity=None,quality='UNKNOWN',parent=incubation.data['id']);self.assertEqual(hatch.status_code,201,hatch.data)
        self.assertIsNone(hatch.data['availability']['available_confirmed']);self.assertEqual(self.live(),1000)
        detail=self.api.get(PREFIX+'reproductions/'+str(self.rep.pk)+'/').data
        self.assertIsNone(detail['fertilization_rate']);self.assertIsNone(detail['hatching_rate'])
        self.assertEqual(self.step(kind='SPAWN',quantity=None,quality='UNKNOWN').status_code,201)
    def test_simple_hatch_without_spawn_is_valid_and_not_live_stock(self):
        hatch=self.hatch();self.assertEqual(self.availability(hatch)['available_confirmed'],100);self.assertEqual(self.live(),1000)
    def test_estimated_and_unknown_hatches_require_explicit_confirmation(self):
        for quality,quantity in [('ESTIMATED',500),('UNKNOWN',None)]:
            baseline=self.live()
            hatch=self.hatch(quality=quality,quantity=quantity)
            denied=self.place(hatch,10);self.assertEqual(denied.status_code,409,denied.data)
            self.assertEqual(self.live(),baseline)
            counted=self.count(hatch,30);self.assertEqual(counted.status_code,201,counted.data)
            placed=self.place(hatch,10);self.assertEqual(placed.status_code,201,placed.data)
        self.assertEqual(self.live(),1020)
    def test_inline_explicit_count_and_placement_are_atomic(self):
        hatch=self.hatch(quality='ESTIMATED',quantity=1000)
        r=self.place(hatch,20,confirm_total=30,confirmation_reason='Comptage réel, différent de l’estimation')
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['hatch_availability']['available_confirmed'],10)
        self.assertEqual(self.live(),1020);self.assertEqual(BreedingFact.objects.filter(kind='COUNT').count(),1)
        another=self.hatch(quality='UNKNOWN',quantity=None);before=BreedingFact.objects.count()
        self.assertEqual(self.place(another,20,confirm_total=10,confirmation_reason='Test incohérent').status_code,409)
        self.assertEqual(BreedingFact.objects.count(),before)
    def test_confirmed_zero_is_not_unknown_and_cannot_supply_positive_stock(self):
        hatch=self.hatch(quantity=0);self.assertEqual(self.availability(hatch)['available_confirmed'],0)
        self.assertEqual(self.place(hatch,1).status_code,409)
    def test_two_partial_placements_no_reuse_and_existing_cohorts(self):
        hatch=self.hatch();first=self.place(hatch,40);second=self.place(hatch,60)
        self.assertEqual(first.status_code,201,first.data);self.assertEqual(second.status_code,201,second.data)
        self.assertEqual(self.live(),1100);self.assertEqual(self.availability(hatch)['available_confirmed'],0)
        self.assertEqual(self.place(hatch,1).status_code,409);self.assertEqual(self.live(),1100)
        child=Cohort.objects.get(pk=first.data['cohort']);self.assertEqual(child.species_id,self.species.pk)
        self.assertEqual(child.origin,{'type':'HATCH','reference':'BREEDING:'+str(hatch.pk)})
        self.assertEqual(self.api.get(PREFIX+'cohorts/'+str(child.pk)+'/').status_code,200)
    def test_subsequent_transfer_mortality_weights_and_health_use_existing_stock(self):
        hatch=self.hatch();placed=self.place(hatch,100);self.assertEqual(placed.status_code,201,placed.data)
        child=Cohort.objects.get(pk=placed.data['cohort']);src=child.positions.get(installation=self.site)
        moved=self.post('transfers/',cohort=str(child.pk),source=str(self.site.pk),destination=str(self.other_site.pk),count=30,occurred_at=timezone.now().isoformat(),reason='Transfert existant',expected_versions={'cohort':child.version,'source_position':src.version,'destination_position':0})
        self.assertEqual(moved.status_code,201,moved.data);self.assertEqual(self.live(),1100)
        loss=self.stock('mortalities/',installation=str(self.other_site.pk),cohort=str(child.pk),quantity=5)
        self.assertEqual(loss.status_code,201,loss.data);self.assertEqual(self.live(),1095)
        at=timezone.now().isoformat();weight=self.post('weights/',cohort=str(child.pk),occurred_at=at,count_reference_at=at,direct_mean_g='1.5')
        self.assertEqual(weight.status_code,201,weight.data)
        health=self.post('health-observations/',installation=str(self.other_site.pk),cohort=str(child.pk),occurred_at=at,symptoms='Observation de test')
        self.assertEqual(health.status_code,201,health.data)
        self.assertEqual(self.availability(hatch)['available_confirmed'],0)
        original=BreedingFact.objects.get(pk=placed.data['id']);self.assertEqual(self.void(original).status_code,409)
    def test_uuid_replay_conflict_and_no_duplicate_initial_entry(self):
        hatch=self.hatch();body={'operation_uuid':str(uuid.uuid4()),'schema_version':1,**self.placement_body(hatch,40)}
        one=self.api.post(PREFIX+'fry-placements/',body,format='json');two=self.api.post(PREFIX+'fry-placements/',body,format='json')
        self.assertEqual(one.status_code,201,one.data);self.assertEqual(one.data,two.data);self.assertEqual(two['Idempotent-Replay'],'true')
        self.assertEqual(self.live(),1040);self.assertEqual(BreedingFact.objects.filter(kind='PLACEMENT').count(),1)
        body['quantity']=41;self.assertEqual(self.api.post(PREFIX+'fry-placements/',body,format='json').status_code,409)
    def test_used_cohort_id_and_child_uuid_collision_refused(self):
        hatch=self.hatch();r=self.place(hatch,10,id=str(self.a.pk));self.assertEqual(r.status_code,409,r.data)
        self.assertEqual(self.live(),1000)
    def test_count_cannot_be_reduced_below_allocated_and_prefix_checked(self):
        hatch=self.hatch();self.assertEqual(self.place(hatch,80).status_code,201)
        self.assertEqual(self.count(hatch,70).status_code,409)
        self.assertEqual(self.availability(hatch)['confirmed_total'],100)
        future_count=self.count(hatch,150);self.assertEqual(future_count.status_code,201,future_count.data)
        before_count=(timezone.now()-timezone.timedelta(seconds=10)).isoformat()
        r=self.place(hatch,50,occurred_at=before_count);self.assertEqual(r.status_code,409,r.data)
    def test_antidated_placement_before_hatch_and_reproduction_rejected(self):
        hatch=self.hatch();self.assertEqual(self.place(hatch,10,occurred_at=self.rep_at).status_code,400)
        self.assertEqual(self.step(occurred_at=self.group_at).status_code,409)
        self.assertEqual(self.count(hatch,100,occurred_at=self.rep_at).status_code,400)
    def test_foreign_references_are_refused(self):
        other=User.objects.create_user(username='other-breeding')
        from pisciculture.models import Installation
        foreign=Installation.objects.create(exploitation=other.exploitation,code='FOREIGN',name='Foreign',type='BASSIN')
        self.assertEqual(self.step(installation=str(foreign.pk)).status_code,404)
        self.assertEqual(self.post('reproductions/',id=str(uuid.uuid4()),group=str(uuid.uuid4()),installation=str(self.site.pk),occurred_at=self.rep_at).status_code,404)
        hatch=self.hatch();self.assertEqual(self.place(hatch,10,installation=str(foreign.pk)).status_code,404)
    def test_permissions_revoked_operator_and_extra_cohort_permission(self):
        hatch=self.hatch();user=User.objects.create_user(username='breeding-operator',exploitation=self.farm);member=ExploitationMembership.objects.get(user=user)
        self.api.force_authenticate(user);self.assertEqual(self.step().status_code,403)
        for code in ['aquatic.read','aquatic.reproduction.create','aquatic.fry.stock']:MembershipPermission.objects.create(membership=member,code=code)
        self.assertEqual(self.step().status_code,201);self.assertEqual(self.place(hatch,10).status_code,403)
        MembershipPermission.objects.create(membership=member,code='aquatic.cohorts.manage');self.assertEqual(self.place(hatch,10).status_code,201)
        member.is_active=False;member.save();self.assertEqual(self.api.get(PREFIX+'breeding-facts/').status_code,403)
    def test_void_unconsumed_placement_compensates_once_and_returns_source_availability(self):
        hatch=self.hatch();placed=self.place(hatch,40);self.assertEqual(placed.status_code,201,placed.data)
        fact=BreedingFact.objects.get(pk=placed.data['id']);r=self.void(fact);self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(self.live(),1000);self.assertEqual(self.availability(hatch)['available_confirmed'],100)
        self.assertEqual(Cohort.objects.get(pk=fact.cohort_id).status,'CLOSED')
        self.assertEqual(Event.objects.filter(cohort=fact.cohort).count(),2)
        self.assertEqual(self.void(fact).status_code,409);self.assertEqual(self.place(hatch,100).status_code,201)
        self.assertEqual(self.live(),1100)
    def test_void_parent_with_active_descendants_refused_then_reverse_order_allowed(self):
        spawn=self.step(kind='SPAWN',quantity=None,quality='UNKNOWN');hatch=self.hatch(parent=spawn.data['id'])
        self.assertEqual(self.void(BreedingFact.objects.get(pk=spawn.data['id'])).status_code,409)
        self.assertEqual(self.void(hatch).status_code,201)
        self.assertEqual(self.void(BreedingFact.objects.get(pk=spawn.data['id'])).status_code,201)
        self.assertEqual(self.place(hatch,1).status_code,409)
    def test_group_and_reproduction_status_revisions_are_versioned_owner_only(self):
        closed=self.post('reproduction-revisions/',target=str(self.rep.pk),expected_version=self.rep.version,status='CLOSED',reason='Clôture documentée')
        self.assertEqual(closed.status_code,201,closed.data);self.assertEqual(self.step().status_code,404)
        stale=self.post('reproduction-revisions/',target=str(self.rep.pk),expected_version=self.rep.version,status='OPEN',reason='Reprise')
        self.assertEqual(stale.status_code,409)
        revision=self.post('brood-group-revisions/',target=str(self.group.pk),expected_version=self.group.version,status='INACTIVE',reason='Pause documentée')
        self.assertEqual(revision.status_code,201,revision.data)
        self.assertEqual(self.post('reproductions/',id=str(uuid.uuid4()),group=str(self.group.pk),installation=str(self.site.pk),occurred_at=timezone.now().isoformat()).status_code,404)
    def test_invalid_quality_numbers_confirmation_and_dates(self):
        for quality,n in [('UNKNOWN',0),('COUNTED',None),('ESTIMATED',None),('COUNTED',-1),('COUNTED',1.5),('COUNTED',True)]:self.assertEqual(self.step(quality=quality,quantity=n).status_code,400)
        hatch=self.hatch()
        self.assertEqual(self.place(hatch,0).status_code,400);self.assertEqual(self.place(hatch,1,actually_counted=False).status_code,400)
        self.assertEqual(self.place(hatch,1,occurred_at=(timezone.now()+timezone.timedelta(days=1)).isoformat()).status_code,400)
        self.assertEqual(self.place(hatch,1,confirm_total=50).status_code,400)
        self.assertEqual(self.step(unknown_field='x').status_code,400)
    def test_history_filters_pagination_status_and_no_legacy_finance_mutation(self):
        baseline=self.api.get('/api/dashboard/').data
        self.hatch();self.step(kind='SPAWN',quantity=20,quality='ESTIMATED')
        rows=self.api.get(PREFIX+'breeding-facts/',{'limit':1}).data;self.assertIsNotNone(rows['next_cursor'])
        next_page=self.api.get(PREFIX+'breeding-facts/',{'limit':1,'cursor':rows['next_cursor']});self.assertEqual(next_page.status_code,200,next_page.data)
        self.assertNotEqual(rows['results'][0]['id'],next_page.data['results'][0]['id'])
        self.assertEqual(len(self.api.get(PREFIX+'breeding-facts/',{'species':self.species.pk,'group':str(self.group.pk),'kind':'HATCH'}).data['results']),1)
        self.assertEqual(self.api.get(PREFIX+'breeding-facts/',{'from':timezone.now().isoformat(),'to':self.rep_at}).status_code,400)
        self.assertEqual(self.api.get('/api/dashboard/').data,baseline)
    def test_atomic_rollback_after_cohort_creation(self):
        hatch=self.hatch();before=Command.objects.count()
        with patch('pisciculture.breeding_services.create_fact',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.place(hatch,40)
        self.assertEqual(Cohort.objects.count(),2);self.assertEqual(self.live(),1000);self.assertEqual(Command.objects.count(),before)
    def test_confirmations_are_explicit_json_true_and_not_truthy_defaults(self):
        hatch=self.hatch(quality='UNKNOWN',quantity=None)
        for value in [False,1,'true',None]:
            self.assertEqual(self.place(hatch,1,actually_counted=value).status_code,400)
        self.assertEqual(self.live(),1000)

    def test_count_void_cannot_remove_capacity_in_use(self):
        hatch=self.hatch(quality='UNKNOWN',quantity=None)
        count=self.count(hatch,50);self.assertEqual(count.status_code,201,count.data)
        self.assertEqual(self.place(hatch,20).status_code,201)
        self.assertEqual(self.void(BreedingFact.objects.get(pk=count.data['id'])).status_code,409)
        self.assertEqual(self.availability(hatch)['available_confirmed'],30)

    def test_group_revision_cursor_is_stale_after_metadata_change(self):
        self.post('brood-groups/',id=str(uuid.uuid4()),name='Deuxième groupe',species=self.species.pk,installation=str(self.site.pk),occurred_at=self.group_at)
        first=self.api.get(PREFIX+'brood-groups/',{'limit':1}).data
        self.assertIsNotNone(first['next_cursor'])
        changed=self.post('brood-group-revisions/',target=str(self.group.pk),expected_version=1,name='Nom rectifié',reason='Motif conservé')
        self.assertEqual(changed.status_code,201,changed.data)
        self.assertEqual(self.api.get(PREFIX+'brood-groups/',{'limit':1,'cursor':first['next_cursor']}).status_code,400)

    def test_metadata_revision_history_keeps_reason_before_after_and_tenant(self):
        changed=self.post('brood-group-revisions/',target=str(self.group.pk),expected_version=1,name='Nom corrigé',reason='Correction traçable de test')
        self.assertEqual(changed.status_code,201,changed.data)
        rows=self.api.get(PREFIX+'breeding-revisions/',{'group':str(self.group.pk)}).data['results']
        self.assertEqual(len(rows),1,rows);self.assertEqual(rows[0]['reason'],'Correction traçable de test')
        self.assertEqual(rows[0]['before']['name'],'Géniteurs de test');self.assertEqual(rows[0]['after']['name'],'Nom corrigé')
        self.assertEqual(self.api.get(PREFIX+'breeding-revisions/',{'group':str(uuid.uuid4())}).status_code,404)

    def test_replayed_placement_rechecks_revoked_rights(self):
        hatch=self.hatch();user=User.objects.create_user(username='replay-breeding',exploitation=self.farm);member=ExploitationMembership.objects.get(user=user)
        for code in ['aquatic.read','aquatic.fry.stock','aquatic.cohorts.manage']:MembershipPermission.objects.create(membership=member,code=code)
        self.api.force_authenticate(user)
        body={'operation_uuid':str(uuid.uuid4()),'schema_version':1,**self.placement_body(hatch,20)}
        self.assertEqual(self.api.post(PREFIX+'fry-placements/',body,format='json').status_code,201)
        MembershipPermission.objects.filter(membership=member,code='aquatic.fry.stock').delete()
        self.assertEqual(self.api.post(PREFIX+'fry-placements/',body,format='json').status_code,403)
        self.assertEqual(BreedingFact.objects.filter(kind='PLACEMENT').count(),1)

    def test_downstream_pending_loss_prevents_backdated_introduction(self):
        pending=self.loss(10);self.assertEqual(pending.status_code,201,pending.data)
        hatch=self.hatch();before=self.live();cohorts=Cohort.objects.count()
        earlier=(timezone.now()-timezone.timedelta(minutes=20)).isoformat()
        result=self.place(hatch,10,occurred_at=earlier)
        self.assertEqual(result.status_code,409,result.data)
        self.assertEqual(Cohort.objects.count(),cohorts);self.assertEqual(self.live(),before)

    def test_fry_cohort_uses_shared_feeding_without_a_second_birth_or_expense(self):
        from core.models import CategorieDepense
        from core.supply_models import SupplyProduct,SupplyCategoryBinding
        hatch=self.hatch();placed=self.place(hatch,40)
        self.assertEqual(placed.status_code,201,placed.data);self.assertEqual(Depense.objects.count(),0)
        product=SupplyProduct.objects.create(exploitation=self.farm,kind='FEED',name='Aliment commun de test')
        category=CategorieDepense.objects.create(exploitation=self.farm,nom='Alimentation de test')
        SupplyCategoryBinding.objects.create(category=category,purpose='FEED')
        bought=self.post('feed-purchases/',product=str(product.pk),category=category.pk,quantity_kg='10',amount='20.00',unit='kg',occurred_at=timezone.now().isoformat())
        self.assertEqual(bought.status_code,201,bought.data)
        fed=self.post('feed-distributions/',product=str(product.pk),installation=str(self.site.pk),cohort=placed.data['cohort'],quantity_kg='1',unit='kg',occurred_at=timezone.now().isoformat())
        self.assertEqual(fed.status_code,201,fed.data)
        self.assertEqual(Depense.objects.count(),1);self.assertEqual(self.live(),1040)
        self.assertEqual(self.availability(hatch)['available_confirmed'],60)

    def test_facts_orm_sql_immutable_and_no_delete(self):
        hatch=self.hatch()
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):hatch.delete()
        with self.assertRaises(ValidationError):BreedingFact.objects.filter(pk=hatch.pk).update(quantity=5)
        if connection.vendor=='postgresql':
            with self.assertRaises(DatabaseError),transaction.atomic():
                with connection.cursor() as c:c.execute('UPDATE pisciculture_breedingfact SET quantity=5 WHERE id=%s',[str(hatch.pk)])
        self.assertEqual(self.api.delete(PREFIX+'breeding-facts/').status_code,405)

@skipUnless(connection.vendor=='postgresql','Real PostgreSQL row locking required')
class BreedingConcurrencyTests(BreedingFixture,TransactionTestCase):
    def pair(self,bodies):
        barrier=Barrier(2)
        def send(body):
            close_old_connections()
            try:
                api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait(timeout=15)
                r=api.post(PREFIX+'fry-placements/',body,format='json');return r.status_code,r.data
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(send,bodies))
    def test_parallel_placements_cannot_spend_same_confirmed_fry(self):
        hatch=self.hatch();bodies=[{'operation_uuid':str(uuid.uuid4()),'schema_version':1,**self.placement_body(hatch,60)} for _ in range(2)]
        result=self.pair(bodies);self.assertEqual(sorted(x[0] for x in result),[201,409],result);self.assertEqual(self.live(),1060)
    def test_parallel_identical_uuid_introduces_only_one_cohort(self):
        hatch=self.hatch();body={'operation_uuid':str(uuid.uuid4()),'schema_version':1,**self.placement_body(hatch,40)}
        result=self.pair([body,body]);self.assertEqual(result[0],result[1]);self.assertEqual(result[0][0],201,result);self.assertEqual(self.live(),1040)
