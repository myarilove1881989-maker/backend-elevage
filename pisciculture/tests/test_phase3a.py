import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from unittest.mock import patch
from django.test import TestCase,TransactionTestCase
from django.db import connection,transaction,DatabaseError,close_old_connections
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User,Depense,Lot,CategorieDepense,ExploitationMembership,MembershipPermission,Task
from core.supply_models import SupplyProduct,SupplyOperation,SupplyReceipt,SupplyMovement,SupplyVial
from core.vaccination_models import Vaccination
from pisciculture.models import HealthRecord,SanitaryProduct,Command,Position,StockFact
from pisciculture.tests.test_phase2b import StockFixture
from pisciculture.tests.test_phase1 import PREFIX
from pisciculture.health_services import UNKNOWN_WITHDRAWAL

class HealthFixture(StockFixture):
    def setUp(self):
        super().setUp()
        self.vaccine=SupplyProduct.objects.create(exploitation=self.farm,name='Produit existant de test',kind='VACCINE')
        self.at=(timezone.now()-timezone.timedelta(minutes=15)).isoformat()
    def observe(self,**extra):
        body={'installation':str(self.site.pk),'occurred_at':self.at,'symptoms':'Aspect inhabituel','behavior':'Nage inhabituelle'}
        body.update(extra);return self.post('health-observations/',**body)
    def treatment_body(self,**extra):
        body={'installation':str(self.site.pk),'occurred_at':self.at,'supply_product':str(self.vaccine.pk),
            'quantity':'1.250000','unit':'dose','administration_status':'ADMINISTERED','reason':'Acte constaté, données fictives'}
        body.update(extra);return body
    def treat(self,**extra):return self.post('health-treatments/',**self.treatment_body(**extra))
    def check(self,site=None,cohort=None,**extra):
        return self.api.get(PREFIX+'health-sale-check/',{'installation':str((site or self.site).pk),**({'cohort':str(cohort.pk)} if cohort else {}),**extra})
    def correct(self,row,**replacement):
        return self.post('health-corrections/',target=row.data['id'],reason='Rectification documentée',treatment=self.treatment_body(**replacement))

class HealthTests(HealthFixture,TestCase):
    def test_observation_no_diagnosis_treatment_mortality_or_finance(self):
        before=list(Position.objects.values_list('pk','gross_count','reserved_count'))
        r=self.observe();self.assertEqual(r.status_code,201,r.data)
        self.assertIsNone(r.data['cohort']);self.assertEqual(set(r.data['eligible_cohorts']),{str(self.a.pk),str(self.b.pk)})
        self.assertEqual(HealthRecord.objects.count(),1);self.assertEqual(HealthRecord.objects.filter(kind='TREATMENT').count(),0)
        for model in [Depense,SupplyOperation,SupplyMovement,Vaccination,Task,StockFact]:self.assertEqual(model.objects.count(),0,model)
        self.assertEqual(list(Position.objects.values_list('pk','gross_count','reserved_count')),before)
    def test_identified_cohort_and_empty_symptoms_rules(self):
        r=self.observe(cohort=str(self.a.pk),symptoms='');self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['eligible_cohorts'],[str(self.a.pk)])
        self.assertEqual(self.observe(symptoms='',behavior='').status_code,400)
        self.assertEqual(self.observe(diagnosis='invented').status_code,400)
    def test_treatment_with_and_without_observation_no_extra_expense_stock(self):
        obs=self.observe()
        first=self.treat(observation=obs.data['id']);self.assertEqual(first.status_code,201,first.data)
        second=self.treat();self.assertEqual(second.status_code,201,second.data)
        self.assertEqual(first.data['product_snapshot']['id'],str(self.vaccine.pk))
        self.assertEqual(first.data['administration_status'],'ADMINISTERED')
        self.assertEqual(Depense.objects.count(),0);self.assertEqual(SupplyMovement.objects.count(),0);self.assertEqual(Vaccination.objects.count(),0)
    def test_bought_product_is_never_an_administered_treatment(self):
        lot=Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Terrestre',date_debut=timezone.now().date())
        category=CategorieDepense.objects.create(exploitation=self.farm,nom='Dépense de test')
        expense=Depense.objects.create(lot=lot,categorie=category,montant='10.00')
        op=SupplyOperation.objects.create(exploitation=self.farm,author=self.owner,operation_uuid=uuid.uuid4(),kind='VACCINE_RECEIPT',payload={'synthetic':True},fingerprint='a'*64,occurred_at=timezone.now())
        receipt=SupplyReceipt.objects.create(operation=op,product=self.vaccine,expense=expense,quantity='10',manufacturer_batch='synthetic',expires_on=timezone.now().date()+timezone.timedelta(days=90),vial_count=1,doses_per_vial=10)
        vial=SupplyVial.objects.create(receipt=receipt,ordinal=1)
        SupplyMovement.objects.create(operation=op,line=0,product=self.vaccine,species=self.species,vial=vial,kind='ENTRY',quantity='10')
        self.assertEqual(self.api.get(PREFIX+'health-history/').data['results'],[])
        self.assertEqual(self.treat().status_code,201)
        self.assertEqual(Depense.objects.count(),1);self.assertEqual(SupplyMovement.objects.count(),1);self.assertEqual(SupplyReceipt.objects.get(pk=receipt.pk).quantity,10)
    def test_documented_delay_and_unknown_never_authorize_sale(self):
        row=self.treat(withdrawal_days=2,withdrawal_source='Document fictif de qualification')
        self.assertEqual(row.status_code,201,row.data);self.assertEqual(row.data['precaution']['status'],'ACTIVE')
        self.assertEqual(timezone.datetime.fromisoformat(row.data['withdrawal_until']),timezone.datetime.fromisoformat(self.at)+timezone.timedelta(days=2))
        checked=self.check(cohort=self.a);self.assertEqual(checked.status_code,200,checked.data)
        self.assertEqual(checked.data['status'],'RESTRICTION_RECORDED');self.assertIsNone(checked.data['sale_authorized'])
        unknown=self.treat();self.assertEqual(unknown.data['precaution']['message'],UNKNOWN_WITHDRAWAL)
        self.assertEqual(self.check(cohort=self.a).data['status'],'UNKNOWN_WITHDRAWAL')
    def test_zero_documented_delay_differs_from_unknown_and_ongoing_declared(self):
        self.assertEqual(self.treat(withdrawal_days=0).status_code,400)
        r=self.treat(withdrawal_days=0,withdrawal_source='Source de test',ongoing=True)
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['precaution']['status'],'ELAPSED_RECORDED')
        self.assertEqual(self.check().data['status'],'ONGOING_REVIEW')
        self.assertIsNone(self.check().data['sale_authorized'])
    def test_catalog_reuses_existing_and_new_nonstock_products_without_duplicate(self):
        catalog=self.api.get(PREFIX+'health-products/').data
        self.assertEqual(catalog['products'][0]['id'],str(self.vaccine.pk));self.assertFalse(catalog['stock_automation']);self.assertFalse(catalog['photo_supported']);self.assertFalse(catalog['planning_supported'])
        r=self.post('health-products/',id=str(uuid.uuid4()),name='Autre produit constaté',type='TREATMENT',unit='mL',information='Information saisie, sans recommandation')
        self.assertEqual(r.status_code,201,r.data)
        body=self.treatment_body(unit='mL');body.pop('supply_product');body['sanitary_product']=r.data['id']
        treated=self.post('health-treatments/',**body);self.assertEqual(treated.status_code,201,treated.data)
        self.assertEqual(self.treat(unit='mL').status_code,400)
        duplicate=self.post('health-products/',id=str(uuid.uuid4()),name=self.vaccine.name,type='OTHER',unit='unit')
        self.assertEqual(duplicate.status_code,409);self.assertEqual(SanitaryProduct.objects.count(),1)
    def test_dose_unit_status_and_dates_strict(self):
        for quantity in ['0','-1','NaN','Infinity','1.0000001',True,1.2]:self.assertEqual(self.treat(quantity=quantity).status_code,400,quantity)
        for key in ['quantity','unit','reason','administration_status']:
            body=self.treatment_body();body.pop(key);self.assertEqual(self.post('health-treatments/',**body).status_code,400,key)
        self.assertEqual(self.treat(administration_status='PLANNED').status_code,400)
        self.assertEqual(self.treat(occurred_at=(timezone.now()+timezone.timedelta(days=1)).isoformat()).status_code,400)
        self.assertEqual(self.treat(occurred_at='2026-11-01T10:00:00').status_code,400)
        self.assertEqual(self.observe(photo='https://example.invalid/photo.jpg').status_code,400)
    def test_observation_link_scope_date_and_foreign_references(self):
        obs=self.observe(cohort=str(self.a.pk));self.assertEqual(self.treat(cohort=str(self.b.pk),observation=obs.data['id']).status_code,400)
        self.assertEqual(self.treat(observation=obs.data['id'],occurred_at=(timezone.now()-timezone.timedelta(minutes=30)).isoformat()).status_code,400)
        self.assertEqual(self.treat(observation=str(uuid.uuid4())).status_code,404)
        self.assertEqual(self.treat(supply_product=str(uuid.uuid4())).status_code,404)
        other=User.objects.create_user(username='foreign-health')
        from pisciculture.models import Installation
        foreign=Installation.objects.create(exploitation=other.exploitation,code='X',name='Foreign',type='BASSIN')
        self.assertEqual(self.observe(installation=str(foreign.pk)).status_code,404)
        self.assertEqual(self.observe(cohort=str(uuid.uuid4())).status_code,404)
        p=SupplyProduct.objects.create(exploitation=other.exploitation,kind='VACCINE',name='Other')
        self.assertEqual(self.treat(supply_product=str(p.pk)).status_code,404)
    def test_uuid_replay_collision_and_atomicity(self):
        body={'operation_uuid':str(uuid.uuid4()),'schema_version':1,**self.treatment_body()}
        first=self.api.post(PREFIX+'health-treatments/',body,format='json');second=self.api.post(PREFIX+'health-treatments/',body,format='json')
        self.assertEqual(first.status_code,201,first.data);self.assertEqual(first.data,second.data);self.assertEqual(second['Idempotent-Replay'],'true')
        body['quantity']='2';self.assertEqual(self.api.post(PREFIX+'health-treatments/',body,format='json').status_code,409)
        self.assertEqual(HealthRecord.objects.count(),1)
        with patch('pisciculture.services.Command.objects.create',side_effect=RuntimeError('synthetic')):
            with self.assertRaises(RuntimeError):self.treat()
        self.assertEqual(HealthRecord.objects.count(),1)
    def test_permissions_revocation_and_owner_only_corrections_catalog(self):
        user=User.objects.create_user(username='health-operator',exploitation=self.farm);member=ExploitationMembership.objects.get(user=user)
        self.api.force_authenticate(user);self.assertEqual(self.observe().status_code,403)
        for code in ['aquatic.read','aquatic.health.observe']:MembershipPermission.objects.create(membership=member,code=code)
        self.assertEqual(self.observe().status_code,201);self.assertEqual(self.treat().status_code,403)
        MembershipPermission.objects.create(membership=member,code='aquatic.health.treat')
        row=self.treat();self.assertEqual(row.status_code,201,row.data)
        self.assertEqual(self.correct(row).status_code,403)
        self.assertEqual(self.post('health-products/',id=str(uuid.uuid4()),name='No',type='OTHER',unit='g').status_code,403)
        MembershipPermission.objects.filter(membership=member,code='aquatic.health.treat').delete();self.assertEqual(self.treat().status_code,403)
        member.is_active=False;member.save();self.assertEqual(self.api.get(PREFIX+'health-history/').status_code,403)
    def test_corrections_keep_original_and_do_not_duplicate_precautions(self):
        row=self.treat(withdrawal_days=2,withdrawal_source='Source de test',ongoing=True)
        replacement=self.correct(row,quantity='2',withdrawal_days=0,withdrawal_source='Rectification documentée',ongoing=False)
        self.assertEqual(replacement.status_code,201,replacement.data)
        original=self.api.get(PREFIX+'health-records/'+row.data['id']+'/').data
        self.assertFalse(original['is_current']);self.assertEqual(original['quantity'],'1.250000')
        self.assertEqual(replacement.data['root_id'],row.data['root_id']);self.assertEqual(HealthRecord.objects.count(),2)
        self.assertEqual(len(self.check().data['matches']),2) # two exposed cohorts, only the current record
        self.assertTrue(all(x['record']==replacement.data['id'] for x in self.check().data['matches']))
        self.assertEqual(self.correct(row).status_code,409)
        self.assertEqual(self.api.get(PREFIX+'health-state/').data['results'],[])
    def test_history_filters_multispecies_and_pagination(self):
        self.observe();self.treat(cohort=str(self.a.pk));self.observe(cohort=str(self.b.pk))
        rows=self.api.get(PREFIX+'health-history/',{'limit':1}).data;self.assertIsNotNone(rows['next_cursor'])
        next_page=self.api.get(PREFIX+'health-history/',{'limit':1,'cursor':rows['next_cursor']});self.assertEqual(next_page.status_code,200)
        self.assertNotEqual(rows['results'][0]['id'],next_page.data['results'][0]['id'])
        self.assertEqual(len(self.api.get(PREFIX+'health-history/',{'cohort':str(self.b.pk)}).data['results']),2)
        self.assertEqual(len(self.api.get(PREFIX+'health-history/',{'species':self.species.pk}).data['results']),3)
        self.assertEqual(self.api.get(PREFIX+'health-history/',{'limit':1,'kind':'TREATMENT','cursor':rows['next_cursor']}).status_code,400)
        self.assertEqual(self.api.get(PREFIX+'health-history/',{'from':timezone.now().isoformat(),'to':self.at}).status_code,400)
    def test_restriction_not_applied_to_unrelated_cohort_or_new_arrival(self):
        self.treat(cohort=str(self.a.pk))
        self.assertEqual(self.check(cohort=self.b).data['matches'],[])
        self.treat()
        newcomer=self.create_cohort('NEW',20,started_at=timezone.now().isoformat())
        self.assertEqual(self.check(cohort=newcomer).data['matches'],[])
    def test_transfer_requires_traceability_not_automatic_other_population_restriction(self):
        self.treat(cohort=str(self.a.pk),withdrawal_days=2,withdrawal_source='Source fictive')
        self.assertEqual(self.transfer(100).status_code,201)
        checked=self.check(site=self.other_site,cohort=self.a)
        self.assertEqual(checked.status_code,200,checked.data);self.assertEqual(checked.data['status'],'TRACEABILITY_REVIEW')
        self.assertEqual(checked.data['matches'][0]['scope_match'],'TRACEABILITY_REQUIRED')
        self.assertIsNone(checked.data['sale_authorized'])
    def test_date_only_end_of_day_and_historical_closed_cohort(self):
        old=self.create_cohort('OLD',20,site=self.other_site,started_at=(timezone.now()-timezone.timedelta(days=3)).isoformat())
        lost=self.stock('mortalities/',installation=str(self.other_site.pk),cohort=str(old.pk),quantity=20)
        self.assertEqual(lost.status_code,201,lost.data)
        old.refresh_from_db();old.status='CLOSED';old.save()
        day=timezone.now().date().isoformat()
        r=self.treat(installation=str(self.other_site.pk),cohort=str(old.pk),occurred_at=day+'T00:00:00Z',time_precision='DATE',business_date=day,withdrawal_days=1,withdrawal_source='Source fictive')
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['precaution']['basis'],'END_OF_DAY_CONSERVATIVE')
        self.assertEqual(r.data['precaution']['end_date'],(timezone.now().date()+timezone.timedelta(days=1)).isoformat())
        self.assertEqual(self.treat(time_precision='DATE').status_code,400)
    def test_no_legacy_terrestrial_dashboard_or_task_mutation(self):
        before=self.api.get('/api/dashboard/').data
        self.observe();self.treat();self.assertEqual(self.api.get('/api/dashboard/').data,before)
        self.assertEqual(self.api.get('/api/depenses/').data,[])
        self.assertEqual(Task.objects.count(),0)
    def test_precautions_state_is_paged_and_ignores_history_period(self):
        self.treat();self.treat(ongoing=True)
        first=self.api.get(PREFIX+'health-state/',{'limit':1})
        self.assertEqual(first.status_code,200,first.data);self.assertTrue(first.data['period_independent'])
        self.assertIsNotNone(first.data['next_cursor'])
        second=self.api.get(PREFIX+'health-state/',{'limit':1,'cursor':first.data['next_cursor']})
        self.assertEqual(second.status_code,200,second.data);self.assertNotEqual(first.data['results'][0]['id'],second.data['results'][0]['id'])
        self.assertEqual(self.api.get(PREFIX+'health-state/',{'from':self.at}).status_code,400)
        self.assertEqual(self.api.get(PREFIX+'health-sale-check/').status_code,400)
        self.assertEqual(self.check(as_of=(timezone.now()+timezone.timedelta(days=1)).isoformat()).status_code,400)

    def test_rectification_wrong_kind_and_archived_reference(self):
        row=self.treat()
        bad=self.post('health-corrections/',target=row.data['id'],reason='Wrong kind',observation={'installation':str(self.site.pk),'occurred_at':self.at,'symptoms':'Not a treatment'})
        self.assertEqual(bad.status_code,400)
        self.vaccine.archived=True;self.vaccine.save()
        self.assertEqual(self.treat().status_code,400)
        corrected=self.correct(row,ongoing=False)
        self.assertEqual(corrected.status_code,201,corrected.data)
        self.assertEqual(self.api.get(PREFIX+'health-products/',{'unexpected':'1'}).status_code,400)

    def test_antidated_arrival_does_not_silently_rewrite_exposure(self):
        self.treat()
        newcomer=self.create_cohort('RETRO',20,started_at=(timezone.now()-timezone.timedelta(minutes=30)).isoformat())
        result=self.check(cohort=newcomer)
        self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['status'],'TRACEABILITY_REVIEW')
        self.assertEqual(result.data['matches'][0]['scope_match'],'HISTORICAL_CONTEXT_CHANGED')
        self.assertIsNone(result.data['matches'][0]['candidate_cohort'])
        self.assertIsNone(result.data['sale_authorized'])

    def test_precaution_cursor_refuses_concurrent_change_instead_of_hiding_record(self):
        first=self.treat();self.treat()
        page=self.api.get(PREFIX+'health-state/',{'limit':1}).data
        corrected=self.correct(first,note='Rectification pendant pagination')
        self.assertEqual(corrected.status_code,201,corrected.data)
        stale=self.api.get(PREFIX+'health-state/',{'limit':1,'cursor':page['next_cursor']})
        self.assertEqual(stale.status_code,400)
        fresh=self.api.get(PREFIX+'health-state/').data
        self.assertEqual(len(fresh['results']),2)

    def test_date_only_nested_rectifications_are_validated_without_losing_offset(self):
        old=self.create_cohort('DATENEST',10,site=self.other_site,started_at=(timezone.now()-timezone.timedelta(days=3)).isoformat())
        day=timezone.now().date().isoformat()
        fields={'installation':str(self.other_site.pk),'cohort':str(old.pk),'occurred_at':day+'T00:00:00+02:00','time_precision':'DATE','business_date':day}
        observed=self.observe(**fields);self.assertEqual(observed.status_code,201,observed.data)
        corrected=self.post('health-corrections/',target=observed.data['id'],reason='Rectification date seule',observation={**fields,'symptoms':'Constat précisé'})
        self.assertEqual(corrected.status_code,201,corrected.data);self.assertEqual(corrected.data['business_date'],day)
        treated=self.treat(**fields,withdrawal_days=1,withdrawal_source='Source fictive')
        self.assertEqual(treated.status_code,201,treated.data)
        rectified=self.correct(treated,**fields,withdrawal_days=2,withdrawal_source='Document précisé')
        self.assertEqual(rectified.status_code,201,rectified.data);self.assertEqual(rectified.data['precaution']['basis'],'END_OF_DAY_CONSERVATIVE')
        invalid={**fields,'occurred_at':day+'T10:00:00Z'}
        self.assertEqual(self.correct(rectified,**invalid).status_code,400)

    def test_orm_and_sql_immutable(self):
        row=self.treat();self.assertEqual(row.status_code,201,row.data)
        fact=HealthRecord.objects.get(pk=row.data['id'])
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):HealthRecord.objects.filter(pk=fact.pk).update(note='no')
        with self.assertRaises(ValidationError):fact.delete()
        if connection.vendor=='postgresql':
            with self.assertRaises(DatabaseError),transaction.atomic():
                with connection.cursor() as c:c.execute('UPDATE pisciculture_healthrecord SET note=%s WHERE id=%s',['no',str(fact.pk)])
        self.assertEqual(self.api.delete(PREFIX+'health-records/'+str(fact.pk)+'/').status_code,405)

@skipUnless(connection.vendor=='postgresql','Requires real PostgreSQL concurrency')
class HealthConcurrencyTests(HealthFixture,TransactionTestCase):
    def test_concurrent_same_uuid_creates_one_record(self):
        barrier=Barrier(2)
        body={'operation_uuid':str(uuid.uuid4()),'schema_version':1,**self.treatment_body()}
        def send(_):
            close_old_connections()
            try:
                api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait(timeout=15)
                row=api.post(PREFIX+'health-treatments/',body,format='json');return row.status_code,row.data
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(send,range(2)))
        self.assertEqual(results[0],results[1]);self.assertEqual(results[0][0],201,results);self.assertEqual(HealthRecord.objects.count(),1)
