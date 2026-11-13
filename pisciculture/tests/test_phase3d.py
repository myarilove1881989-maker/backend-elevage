import uuid
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from unittest import skipUnless
from django.test import TestCase,TransactionTestCase
from django.db import connection,close_old_connections
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import Depense,CategorieDepense,Task,User,MembershipPermission,ExploitationMembership,Payment,Vente,Lettrage,AuditEvent
from core.supply_models import SupplyProduct,SupplyCategoryBinding,SupplyMovement
from pisciculture.models import AquaticExpenseLink,AquaticTaskPlan,Harvest,HarvestMovement,HealthRecord,Event,Position,Command
from pisciculture.tests.test_phase3c import CommerceFixture
from pisciculture.tests.test_phase1 import PREFIX

class PlanningFixture(CommerceFixture):
    def setUp(self):
        super().setUp();self.category=CategorieDepense.objects.create(exploitation=self.farm,nom='Entretien')
        self.today=timezone.localdate();self.begin=(self.today-timezone.timedelta(days=2)).isoformat();self.end=(self.today+timezone.timedelta(days=1)).isoformat()
    def cost_body(self,scope='COHORT',**kw):
        return {'category':self.category.pk,'amount':'10.25','date':self.today.isoformat(),'scope':scope,**({'cohort':str(self.a.pk)} if scope=='COHORT' else {'installation':str(self.site.pk)} if scope=='INSTALLATION' else {}),'reason':'Attribution réellement connue',**kw}
    def cost(self,scope='COHORT',**kw):
        r=self.post('finance-expenses/',**self.cost_body(scope,**kw));self.assertEqual(r.status_code,201,r.data);return r
    def summary(self,**kw):
        r=self.api.get(PREFIX+'finance-summary/',{'from':self.begin,'to':self.end,**kw});self.assertEqual(r.status_code,200,r.data);return r.data
    def task_body(self,**kw):return {'title':'Contrôle choisi','kind':'WATER','date':(self.today+timezone.timedelta(days=2)).isoformat(),'installation':str(self.site.pk),'cohort':str(self.a.pk),**kw}
    def plan(self,**kw):
        r=self.post('agenda/',**self.task_body(**kw));self.assertEqual(r.status_code,201,r.data);return r
    def progress(self,p,status='DONE',**kw):return self.post('agenda-status/',target=p.data['id'],expected_version=p.data['version'],status=status,reason='État déclaré par l’auteur',**kw)

class FinancePlanningTests(PlanningFixture,TestCase):
    def test_direct_aquatic_and_farm_costs_separate_no_allocation(self):
        self.cost();self.cost('INSTALLATION',amount='20.00');self.cost('AQUATIC',amount='30.00');self.cost('FARM',amount='40.00')
        s=self.summary();self.assertEqual(s['aquatic_expenses'],'60.25');self.assertEqual(s['farm_common_expenses'],'40.00');self.assertEqual(s['estimated_result'],'-60.25')
        f=self.summary(cohort=str(self.a.pk));self.assertEqual(f['aquatic_expenses'],'10.25');self.assertEqual(f['aquatic_common_expenses'],'30.00');self.assertEqual(f['farm_common_expenses'],'40.00');self.assertFalse(f['basis']['common_in_result'])
        self.assertEqual(Depense.objects.count(),4);self.assertEqual(AquaticExpenseLink.objects.count(),4)
    def test_installation_only_when_explicit_and_species_known_only_for_cohort(self):
        self.cost();self.cost('INSTALLATION');self.cost(cohort=str(self.b.pk),installation=str(self.site.pk))
        self.assertEqual(self.summary(installation=str(self.site.pk))['direct_expenses'],'20.50')
        self.assertEqual(self.summary(species=str(self.species.pk))['direct_expenses'],'20.50')
        self.assertEqual(self.summary(cohort=str(self.a.pk),installation=str(self.site.pk))['direct_expenses'],'0.00')
    def test_food_existing_expense_read_once_and_explicit_assignment(self):
        product=SupplyProduct.objects.create(exploitation=self.farm,kind='FEED',name='Aliment réel')
        SupplyCategoryBinding.objects.create(category=self.category,purpose='FEED')
        r=self.post('feed-purchases/',product=str(product.pk),category=self.category.pk,quantity_kg='10',amount='20.00',unit='kg',occurred_at=timezone.now().isoformat());self.assertEqual(r.status_code,201,r.data)
        s=self.summary();self.assertEqual(s['farm_common_expenses'],'20.00');self.assertEqual(s['aquatic_expenses'],'0.00')
        assigned=self.post('finance-assignments/',expense=r.data['expense_id'],expected_version=0,scope='AQUATIC',reason='Achat réservé à cette activité');self.assertEqual(assigned.status_code,201,assigned.data)
        self.assertEqual(self.summary()['aquatic_expenses'],'20.00');self.assertEqual(self.summary()['farm_common_expenses'],'0.00');self.assertEqual(Depense.objects.count(),1);self.assertEqual(SupplyMovement.objects.count(),1)
        edit=self.post('finance-expense-revisions/',target=assigned.data['id'],expected_version=1,**self.cost_body('AQUATIC',amount='99.00'));self.assertEqual(edit.status_code,409)
    def test_sales_and_allocations_not_added_twice_and_balance_cutoff(self):
        h=self.harvest();self.assertEqual(self.sell(h,'30.000000').status_code,201);self.assertEqual(self.sell(h,'45.000000').status_code,201)
        s=self.summary();self.assertEqual(s['billed_revenue'],'187.50');self.assertEqual(s['received_payments'],'0.00');self.assertEqual(s['client_balance_as_of'],'187.50')
        sale=Vente.objects.order_by('pk').first()
        for n in ['20.00','55.00']:
            self.assertEqual(self.post('commerce-payments/',sale=sale.pk,amount=n,occurred_at=timezone.now().isoformat()).status_code,201)
        s=self.summary();self.assertEqual(s['billed_revenue'],'187.50');self.assertEqual(s['received_payments'],'75.00');self.assertEqual(s['client_balance_as_of'],'112.50')
        rows=self.api.get(PREFIX+'finance-revenues/',{'from':self.begin,'to':self.end}).data['results'];self.assertEqual(len(rows),2)
    def test_prior_invoice_payment_in_period_different_from_revenue(self):
        h=self.harvest();self.sell(h);sale=Vente.objects.get()
        # Shift the invoice date using the raw test manager, keeping one legacy ledger.
        from core.audit import AuditedQuerySet
        AuditedQuerySet(Vente).filter(pk=sale.pk).update(date=self.today-timezone.timedelta(days=10))
        self.post('commerce-payments/',sale=sale.pk,amount='20.00',occurred_at=timezone.now().isoformat())
        s=self.summary();self.assertEqual(s['billed_revenue'],'0.00');self.assertEqual(s['received_payments'],'20.00');self.assertEqual(s['client_balance_as_of'],'55.00')
        rows=self.api.get(PREFIX+'finance-revenues/',{'from':self.begin,'to':self.end}).data['results'];self.assertEqual(len(rows),1);self.assertFalse(rows[0]['invoice_in_period']);self.assertEqual(rows[0]['received_in_period'],'20.00')
    def test_expense_revision_one_economic_row_with_audit_and_version(self):
        original=self.cost();changed=self.post('finance-expense-revisions/',target=original.data['id'],expected_version=1,**self.cost_body(amount='12.50'))
        self.assertEqual(changed.status_code,201,changed.data);self.assertEqual(Depense.objects.count(),1);self.assertEqual(self.summary()['aquatic_expenses'],'12.50')
        hist=self.api.get(PREFIX+'finance-history/',{'target':original.data['id']});self.assertEqual(hist.status_code,200,hist.data);self.assertTrue(any(x['reason']=='Attribution réellement connue' for x in hist.data['results']));self.assertTrue(all(x['actor_name']==self.owner.username for x in hist.data['results']))
        self.assertEqual(self.post('finance-expense-revisions/',target=original.data['id'],expected_version=1,**self.cost_body()).status_code,409)
    def test_categories_reused_and_new_open_category(self):
        data=self.cost_body('AQUATIC');data.pop('category');data['new_category']='Énergie libre'
        r=self.post('finance-expenses/',**data);self.assertEqual(r.status_code,201,r.data);self.assertEqual(CategorieDepense.objects.filter(nom='Énergie libre').count(),1)
        self.assertEqual(self.api.get(PREFIX+'finance-categories/').status_code,200)
    def test_expense_uuid_replay_and_different_payload(self):
        data={**self.cost_body(),'operation_uuid':str(uuid.uuid4())};a=self.post('finance-expenses/',**data);b=self.post('finance-expenses/',**data);self.assertEqual(a.status_code,201,a.data);self.assertEqual(a.data,b.data);self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(self.post('finance-expenses/',**{**data,'amount':'11.00'}).status_code,409)
    def test_foreign_references_and_bad_scopes_refused(self):
        other=User.objects.create_user(username='foreign-finance');cat=CategorieDepense.objects.create(exploitation=other.exploitation,nom='Secret')
        self.assertEqual(self.post('finance-expenses/',**self.cost_body(category=cat.pk)).status_code,404)
        self.assertEqual(self.post('finance-expenses/',**self.cost_body('FARM',cohort=str(self.a.pk))).status_code,400)
        self.assertEqual(self.post('finance-expenses/',**self.cost_body(amount=1.2)).status_code,400)
    def test_finance_filters_and_pagination_signed_revision(self):
        self.cost();self.cost('AQUATIC');p={'from':self.begin,'to':self.end,'limit':1}
        r=self.api.get(PREFIX+'finance-expenses/',p);self.assertEqual(r.status_code,200,r.data);self.assertIsNotNone(r.data['next_cursor'])
        second=self.api.get(PREFIX+'finance-expenses/',{**p,'cursor':r.data['next_cursor']});self.assertEqual(second.status_code,200,second.data)
        self.cost('FARM');self.assertEqual(self.api.get(PREFIX+'finance-expenses/',{**p,'cursor':r.data['next_cursor']}).status_code,400)
    def test_planned_tasks_all_types_never_create_business_facts(self):
        models=[Depense,Vente,Payment,Event,SupplyMovement,HealthRecord,Harvest,HarvestMovement]
        before=[m.objects.count() for m in models]
        for kind in ['WATER','WEIGHT','FEED','OBSERVATION','TREATMENT','REPRODUCTION','HATCH_CHECK','HARVEST','OTHER']:
            p=self.plan(kind=kind);r=self.progress(p);self.assertEqual(r.status_code,201,r.data);self.assertFalse(r.data['business_effect_created'])
        self.assertEqual([m.objects.count() for m in models],before);self.assertEqual(Task.all_tasks.count(),9);self.assertEqual(Task.objects.count(),0)
    def test_existing_terrestrial_agenda_cannot_read_or_change_aquatic_task(self):
        p=self.plan();id=p.data['task_id'];r=self.api.get('/api/tasks/');self.assertEqual(r.status_code,200,r.data)
        rows=r.data if isinstance(r.data,list) else r.data['results'];self.assertFalse(any(x['id']==id for x in rows))
        self.assertEqual(self.api.patch('/api/tasks/'+str(id)+'/',{'title':'Wrong','expected_version':1},format='json').status_code,404)
    def test_task_edit_cancel_and_history_audited(self):
        p=self.plan();edited=self.post('agenda-revisions/',target=p.data['id'],expected_version=1,reason='Date corrigée',**self.task_body(title='Titre rectifié'))
        self.assertEqual(edited.status_code,201,edited.data);cancel=self.progress(edited,'CANCELLED');self.assertEqual(cancel.status_code,201,cancel.data);self.assertEqual(cancel.data['cancellation_reason'],'État déclaré par l’auteur')
        self.assertEqual(self.progress(cancel).status_code,409)
        self.assertEqual(self.api.get(PREFIX+'agenda-history/',{'target':p.data['id']}).status_code,200)
    def test_task_optional_time_and_due_in_app_reminder(self):
        p=self.plan(reminder_at=(timezone.now()-timezone.timedelta(minutes=1)).isoformat());self.assertIsNone(p.data['scheduled_at']);self.assertTrue(p.data['reminder_due'])
        r=self.api.get(PREFIX+'agenda/',{'reminders':'true','from':self.begin,'to':self.today.isoformat()});self.assertEqual(len(r.data['results']),1)
        self.progress(p);self.assertEqual(self.api.get(PREFIX+'agenda/',{'reminders':'true'}).data['results'],[])
    def test_explicit_link_existing_water_fact_no_second_act(self):
        w=self.post('water/',installation=str(self.site.pk),occurred_at=timezone.now().isoformat(),temperature_c='24.00')
        if w.status_code==404:
            w=self.post('water-readings/',installation=str(self.site.pk),occurred_at=timezone.now().isoformat(),temperature_c='24.00')
        self.assertEqual(w.status_code,201,w.data)
        p=self.plan(cohort=None);r=self.progress(p,actual_kind='WATER',actual_id=w.data['id']);self.assertEqual(r.status_code,201,r.data);self.assertEqual(r.data['actual_id'],w.data['id'])
    def test_existing_water_link_uses_recorded_population_scope(self):
        w=self.post('water-readings/',installation=str(self.site.pk),occurred_at=timezone.now().isoformat(),temperature_c='24.00');self.assertEqual(w.status_code,201,w.data)
        p=self.plan();r=self.progress(p,actual_kind='WATER',actual_id=w.data['id']);self.assertEqual(r.status_code,201,r.data)
        self.assertEqual(r.data['actual_id'],w.data['id'])

    def test_task_version_replay_and_permissions_revocation(self):
        data={**self.task_body(),'operation_uuid':str(uuid.uuid4())};a=self.post('agenda/',**data);b=self.post('agenda/',**data);self.assertEqual(a.data,b.data);self.assertEqual(Task.all_tasks.count(),1)
        employee=User.objects.create_user(username='finance-reader',exploitation=self.farm);member=ExploitationMembership.objects.get(user=employee);MembershipPermission.objects.create(membership=member,code='aquatic.read');self.api.force_authenticate(employee)
        self.assertEqual(self.post('finance-expenses/',**self.cost_body()).status_code,403);self.assertEqual(self.post('agenda/',**self.task_body()).status_code,403)
        MembershipPermission.objects.create(membership=member,code='aquatic.agenda.plan');created=self.post('agenda/',**self.task_body());self.assertEqual(created.status_code,201,created.data)
        member.is_active=False;member.save();self.assertEqual(self.post('agenda/',**{**self.task_body(),'operation_uuid':str(uuid.uuid4())}).status_code,403)

    def test_mixed_client_payment_counts_only_aquatic_allocations(self):
        from core.models import Lot
        h=self.harvest();self.sell(h);aq=Vente.objects.get()
        landlot=Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Vrai lot terrestre',date_debut=self.today)
        land=Vente.objects.create(lot=landlot,client=self.customer,quantite=1,prix_unitaire='30.00',date=self.today)
        p=Payment.objects.create(client=self.customer,exploitation=self.farm,date=self.today,montant='50.00')
        Lettrage.objects.create(payment=p,vente=land,montant='30.00');Lettrage.objects.create(payment=p,vente=aq,montant='20.00')
        result=self.summary();self.assertEqual(result['billed_revenue'],'75.00');self.assertEqual(result['received_payments'],'20.00');self.assertEqual(result['client_balance_as_of'],'55.00')
    def test_health_treatment_does_not_suddenly_become_expense(self):
        product=SupplyProduct.objects.create(exploitation=self.farm,name='Produit constaté',kind='VACCINE')
        r=self.post('health-treatments/',installation=str(self.site.pk),occurred_at=timezone.now().isoformat(),supply_product=str(product.pk),quantity='1.000000',unit='dose',administration_status='ADMINISTERED',reason='Traitement enregistré')
        self.assertEqual(r.status_code,201,r.data);self.assertEqual(self.summary()['aquatic_expenses'],'0.00');self.assertEqual(Depense.objects.count(),0)
    def test_foreign_tasks_and_actual_references_are_refused(self):
        p=self.plan();other=User.objects.create_user(username='foreign-plan');api=APIClient();api.force_authenticate(other)
        from pisciculture.models import Activity
        Activity.objects.create(exploitation=other.exploitation,enabled=True)
        r=api.post(PREFIX+'agenda-status/',{'schema_version':1,'operation_uuid':str(uuid.uuid4()),'target':p.data['id'],'expected_version':1,'status':'DONE','reason':'Autre'},format='json');self.assertEqual(r.status_code,404)
        self.assertEqual(self.progress(p,actual_kind='WATER',actual_id=str(uuid.uuid4())).status_code,404)
    def test_incoherent_time_reminder_and_unknown_fields_refused(self):
        future=self.today+timezone.timedelta(days=2)
        at=timezone.now()+timezone.timedelta(days=2)
        self.assertEqual(self.post('agenda/',**self.task_body(scheduled_at=at.isoformat(),reminder_at=(at+timezone.timedelta(hours=1)).isoformat())).status_code,400)
        self.assertEqual(self.post('agenda/',**self.task_body(scheduled_at='2026-11-15T12:00:00')).status_code,400)
        self.assertEqual(self.post('agenda/',**self.task_body(stock_count=50)).status_code,400)
        self.assertEqual(self.api.get(PREFIX+'finance-summary/',{'from':'bad'}).status_code,400)
    def test_land_task_and_aquatic_task_have_one_status_model_separate_routes(self):
        land=Task.objects.create(exploitation=self.farm,title='Tâche terrestre réelle',date=self.today)
        aq=self.plan();self.assertEqual(Task.objects.count(),1);self.assertEqual(Task.all_tasks.count(),2)
        self.assertEqual(Task.objects.get().pk,land.pk);self.assertEqual(Task.all_tasks.get(pk=aq.data['task_id']).status,'TODO')
        self.assertEqual(self.progress(aq).status_code,201)
        land.refresh_from_db();self.assertEqual(land.status,'TODO')
    def test_task_history_and_expense_amount_reads_do_not_mutate_business_revision(self):
        p=self.plan();self.cost();self.farm.refresh_from_db();version=self.farm.business_revision;count=Command.objects.count()
        self.summary();self.api.get(PREFIX+'agenda/',{'from':self.begin,'to':self.end});self.api.get(PREFIX+'agenda-history/',{'target':p.data['id']})
        self.farm.refresh_from_db();self.assertEqual(self.farm.business_revision,version);self.assertEqual(Command.objects.count(),count)

    def test_expense_existing_permission_and_revocation_before_uuid_replay(self):
        employee=User.objects.create_user(username='expense-grants',exploitation=self.farm);member=ExploitationMembership.objects.get(user=employee)
        for code in ['aquatic.read','aquatic.finances.manage']:MembershipPermission.objects.create(membership=member,code=code)
        self.api.force_authenticate(employee);body={**self.cost_body(),'operation_uuid':str(uuid.uuid4())}
        self.assertEqual(self.post('finance-expenses/',**body).status_code,403)
        grant=MembershipPermission.objects.create(membership=member,code='expenses.create')
        self.assertIn('expenses.create',self.api.get(PREFIX+'capabilities/').data['finance_write'])
        r=self.post('finance-expenses/',**body);self.assertEqual(r.status_code,201,r.data)
        grant.delete();self.assertEqual(self.post('finance-expenses/',**body).status_code,403);self.assertEqual(Depense.objects.count(),1)
    def test_task_author_and_assignee_are_checked_with_module_permissions(self):
        p=self.plan();employee=User.objects.create_user(username='task-grants',exploitation=self.farm);member=ExploitationMembership.objects.get(user=employee)
        for code in ['aquatic.read','aquatic.agenda.plan','aquatic.agenda.progress']:MembershipPermission.objects.create(membership=member,code=code)
        self.api.force_authenticate(employee)
        self.assertEqual(self.progress(p).status_code,403)
        r=self.post('agenda-revisions/',target=p.data['id'],expected_version=1,reason='Wrong author',**self.task_body());self.assertEqual(r.status_code,403)
        own=self.plan();self.assertEqual(self.progress(own).status_code,201)

class PlanningConcurrency(PlanningFixture,TransactionTestCase):
    @skipUnless(connection.vendor=='postgresql','PostgreSQL required')
    def test_one_task_uuid_concurrent_creates_one_core_task(self):
        body={'schema_version':1,'operation_uuid':str(uuid.uuid4()),**self.task_body()};barrier=Barrier(2)
        def run(_):
            close_old_connections();api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait()
            try:
                r=api.post(PREFIX+'agenda/',body,format='json');return r.status_code,r.data
            finally:close_old_connections()
        with ThreadPoolExecutor(2) as pool:rows=list(pool.map(run,range(2)))
        self.assertEqual([r[0] for r in rows],[201,201]);self.assertEqual(rows[0][1],rows[1][1]);self.assertEqual(Task.all_tasks.count(),1)

    @skipUnless(connection.vendor=='postgresql','PostgreSQL required')
    def test_same_expense_uuid_concurrent_one_economic_row(self):
        data={'schema_version':1,'operation_uuid':str(uuid.uuid4()),**self.cost_body()};barrier=Barrier(2)
        def run(_):
            close_old_connections();api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));barrier.wait()
            try:
                r=api.post(PREFIX+'finance-expenses/',data,format='json');return r.status_code,r.data
            finally:close_old_connections()
        with ThreadPoolExecutor(2) as pool:rows=list(pool.map(run,range(2)))
        self.assertEqual([r[0] for r in rows],[201,201]);self.assertEqual(rows[0][1],rows[1][1]);self.assertEqual(Depense.objects.count(),1)
