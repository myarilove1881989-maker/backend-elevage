import json,os,uuid,time
from pathlib import Path
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User,Espece,Depense,AuditEvent
P='/api/pisciculture/v1/'

class VolumeQualification(TestCase):
    """Read benchmark; bulk ledger-free task/cost fixtures are explicit, not business proofs."""
    def test_volume_pagination_filters_and_query_measurements(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        from core.models import Task,CategorieDepense
        from pisciculture.models import Activity,Installation,AquaticSpeciesProfile,AquaticTaskPlan,AquaticExpenseLink
        owner=User.objects.create_user(username='synthetic-volume');farm=owner.exploitation;api=APIClient();api.force_authenticate(owner)
        Activity.objects.create(exploitation=farm,enabled=True)
        species=Espece.objects.create(exploitation=farm,nom='Espèce synthétique volume');AquaticSpeciesProfile.objects.create(exploitation=farm,species=species)
        sites=[Installation(exploitation=farm,code='V'+str(i),name='Volume '+str(i),type='BASSIN') for i in range(30)]
        Installation.objects.bulk_create(sites)
        started=(timezone.now()-timezone.timedelta(days=2)).isoformat()
        for i in range(100):
            response=api.post(P+'cohorts/',{'schema_version':1,'operation_uuid':str(uuid.uuid4()),'id':str(uuid.uuid4()),'code':'V'+str(i),'name':'Cohorte '+str(i),'species':species.pk,'origin':{'type':'OTHER'},'started_at':started,'initial_count':100,'allocations':[{'installation':str(sites[i%30].pk),'count':100}],'reason':'Fixture de volume par API'},format='json')
            self.assertEqual(response.status_code,201,response.data)
        day=timezone.localdate()
        tasks=[Task(exploitation=farm,title='Volume '+str(i),date=day,created_by=owner,assigned_to=owner) for i in range(2000)]
        Task.all_tasks.bulk_create(tasks)
        plans=[AquaticTaskPlan(exploitation=farm,task=t,kind='OTHER',actor=owner,installation=sites[i%30]) for i,t in enumerate(tasks)]
        AquaticTaskPlan.objects.bulk_create(plans)
        category=CategorieDepense.objects.create(exploitation=farm,nom='Volume synthétique')
        costs=[Depense(shared_exploitation=farm,shared_purpose='AQUATIC',categorie=category,montant='1.01',date=day) for _ in range(300)]
        Depense.objects.bulk_create(costs)
        AquaticExpenseLink.objects.bulk_create([AquaticExpenseLink(exploitation=farm,expense=x,scope='AQUATIC',actor=owner) for x in costs])
        audit_rows=[AuditEvent(exploitation_id=farm.pk,actor_user_id=owner.pk,action='UPDATE',entity_type='core.task',entity_id=str(tasks[0].pk),applied_at=timezone.now(),reason_text='Fixture historique de lecture',before_data={'title':'avant'},after_data={'title':'après'}) for _ in range(2000)]
        for event in audit_rows:event.save()
        output={'fixture':{'installations':30,'cohorts_created_via_api':100,'initial_fish':10000,'tasks_bulk':2000,'shared_costs_bulk':300,'audit_rows_validated_save':2000},'measurements':[],'method':'Local PostgreSQL exFAT; warm reads repeated three times; fixtures bulk only for reading, not idempotence/financial proof; no universal SLA.'}
        def measure(path,params):
            times=[];counts=[];sizes=[]
            for _ in range(3):
                begin=time.perf_counter()
                with CaptureQueriesContext(connection) as queries:r=api.get(P+path,params)
                elapsed=time.perf_counter()-begin;self.assertEqual(r.status_code,200,getattr(r,'data',r.status_code))
                times.append(round(elapsed,4));counts.append(len(queries));sizes.append(len(r.content))
            self.assertLessEqual(max(counts),30,(path,counts))
            output['measurements'].append({'route':path,'filters':params,'seconds':times,'query_counts':counts,'response_bytes':sizes})
            return r
        measure('dashboard/',{})
        r=measure('cohorts/',{'limit':100});self.assertEqual(len(r.data['results']),100)
        r=measure('agenda/',{'limit':100});self.assertEqual(len(r.data['results']),100);self.assertTrue(r.data['next_cursor'])
        seen={x['id'] for x in r.data['results']};pages=1;cursor=r.data['next_cursor']
        while cursor:
            row=api.get(P+'agenda/',{'limit':100,'cursor':cursor});self.assertEqual(row.status_code,200,row.data)
            ids={x['id'] for x in row.data['results']};self.assertFalse(seen&ids);seen|=ids;pages+=1;cursor=row.data['next_cursor']
        self.assertEqual(len(seen),2000);self.assertEqual(pages,20)
        filtered=measure('agenda/',{'installation':str(sites[0].pk),'limit':100});self.assertEqual(len(filtered.data['results']),67)
        measure('agenda-history/',{'target':str(plans[0].pk),'limit':100})
        totals=measure('finance-summary/',{});self.assertEqual(totals.data['aquatic_expenses'],'303.00')
        measure('finance-expenses/',{'limit':100})
        output['pagination']={'task_pages':pages,'unique_task_ids':len(seen),'duplicates':0,'filtered_task_rows':67}
        folder=os.environ.get('ELEVAGE_QUALIFICATION_DIR')
        if folder:
            out=Path(folder);assert out.drive.upper()=='D:';(out/'evidence/performance.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


from pisciculture.tests.test_phase3c import CommerceFixture

class FinancialVolumeQualification(CommerceFixture,TestCase):
    def test_many_partial_sales_and_payments_keep_exact_totals_and_pages(self):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        h=self.harvest()
        for _ in range(50):
            r=self.sell(h,'1.000000',count=1,price={'client':self.customer.pk,'unit_price':'1.23'});self.assertEqual(r.status_code,201,r.data)
            from core.models import Vente
            sale=Vente.objects.order_by('-pk').first()
            paid=self.post('commerce-payments/',sale=sale.pk,amount='0.50',occurred_at=timezone.now().isoformat());self.assertEqual(paid.status_code,201,paid.data)
        output={'fixture':{'partial_sales':50,'payments':50,'harvest_kg':'100','sold_kg':'50','remaining_kg':'50','billed':'61.50','received':'25.00','balance':'36.50'},'measurements':[]}
        for route,params in [('finance-summary/',{}),('finance-revenues/',{'limit':25})]:
            times=[];counts=[]
            for _ in range(3):
                begin=time.perf_counter()
                with CaptureQueriesContext(connection) as queries:r=self.api.get(P+route,params)
                times.append(round(time.perf_counter()-begin,4));counts.append(len(queries));self.assertEqual(r.status_code,200,r.data)
            self.assertLessEqual(max(counts),30,(route,counts))
            output['measurements'].append({'route':route,'seconds':times,'query_counts':counts})
            if route=='finance-summary/':
                for key,value in {'billed_revenue':'61.50','received_payments':'25.00','client_balance_as_of':'36.50'}.items():self.assertEqual(r.data[key],value)
            else:
                self.assertEqual(len(r.data['results']),25);self.assertTrue(r.data['next_cursor'])
                second=self.api.get(P+route,{'limit':25,'cursor':r.data['next_cursor']});self.assertEqual(second.status_code,200,second.data);self.assertEqual(len(second.data['results']),25)
                self.assertFalse({x['sale_id'] for x in r.data['results']}&{x['sale_id'] for x in second.data['results']})
        folder=os.environ.get('ELEVAGE_QUALIFICATION_DIR')
        if folder:
            out=Path(folder);assert out.drive.upper()=='D:';(out/'evidence/performance-financial.json').write_text(json.dumps(output,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
