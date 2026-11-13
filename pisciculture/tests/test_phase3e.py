"""Synthetic qualification only: no production requests, no new product behavior."""
import json,os,uuid,time
from pathlib import Path
from decimal import Decimal
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import Espece,User,Depense,Vente,Payment,Lettrage,Lot,Client,AuditEvent
from core.supply_models import SupplyProduct,SupplyMovement
from pisciculture.models import Cohort,Position,Event,HealthRecord,Harvest,HarvestMovement,Command
from pisciculture.commerce import balance as harvested_balance
from pisciculture.feeding import balance as feed_balance
P='/api/pisciculture/v1/'

class CompleteCycleQualification(TestCase):
    def setUp(self):
        self.steps=[];self.owner=User.objects.create_user(username='synthetic-global-qualification');self.farm=self.owner.exploitation
        self.api=APIClient();self.api.force_authenticate(self.owner);self.base=timezone.now()-timezone.timedelta(days=90)
    def tearDown(self):
        folder=os.environ.get('ELEVAGE_QUALIFICATION_DIR')
        if folder:
            out=Path(folder);assert out.drive.upper()=='D:'
            (out/'evidence/cycle-api.json').write_text(json.dumps(self.steps,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')
    def at(self,day,minute=0):return (self.base+timezone.timedelta(days=day,minutes=minute)).isoformat()
    def write(self,label,path,**data):
        begin=time.perf_counter();r=self.api.post(P+path,{'schema_version':1,'operation_uuid':str(uuid.uuid4()),**data},format='json')
        self.steps.append({'step':label,'route':path,'status':r.status_code,'seconds':round(time.perf_counter()-begin,4),'response':getattr(r,'data',{'http_status':r.status_code})})
        self.assertEqual(r.status_code,201,getattr(r,'data',r.status_code));return r.data
    def read(self,path,**params):
        r=self.api.get(P+path,params);self.assertEqual(r.status_code,200,r.data);return r.data
    def state(self,expected,label):
        s=self.read('stock-state/');self.assertEqual(s['live_count'],expected)
        self.steps.append({'check':label,'living_fish':s['live_count'],'unassigned':s['unassigned_losses'],'expense_rows':Depense.objects.count(),'sale_rows':Vente.objects.count(),'payment_rows':Payment.objects.count()});return s
    def review(self,at,cohort=None,site=None,harvest=None):
        d={'as_of':at,**({'harvest':harvest} if harvest else {'cohort':cohort,'installation':site})}
        r=self.read('commerce-check/',**d)
        return {'health_token':r['health_token'],'human_verified':True,'verification_reason':'Revue documentaire fictive de qualification, sans recommandation médicale'}
    def test_coherent_28_step_cycle_with_financial_and_stock_conservation(self):
        self.steps.append({'step':1,'synthetic_farm_created':True})
        self.write(2,'activation/',enabled=True)
        sp=self.write(3,'species/',id=str(uuid.uuid4()),name='Tilapia synthétique')['species']
        sites=[]
        for code,kind in [('N','BASSIN'),('S','ETANG'),('ALEV','BAC')]:
            sites.append(self.write(3,'installations/',id=str(uuid.uuid4()),code=code,name=code,type=kind)['id'])
        n,s,nursery=sites
        c=self.write(4,'cohorts/',id=str(uuid.uuid4()),code='GROSS',name='Juvéniles de grossissement',species=sp,origin={'type':'PURCHASE','reference':'Lot fictif déjà juvénile'},started_at=self.at(0),initial_count=1000,allocations=[{'installation':n,'count':600},{'installation':s,'count':400}],reason='Comptage physique initial')['id']
        self.state(1000,'initial allocation 600+400 without double count')
        cohort=Cohort.objects.get(pk=c);positions={str(x.installation_id):x for x in Position.objects.filter(cohort=cohort)}
        self.write(6,'transfers/',cohort=c,source=n,destination=s,count=150,occurred_at=self.at(1),reason='Répartition mesurée',expected_versions={'cohort':cohort.version,'source_position':positions[n].version,'destination_position':positions[s].version})
        self.state(1000,'transfer conservation');self.assertEqual(Position.objects.get(cohort_id=c,installation_id=n).gross_count,450)
        w=self.write(7,'weights/',cohort=c,occurred_at=self.at(45),count_reference_at=self.at(45),sample_count=20,mass_kg='5.000000')
        self.assertEqual(Decimal(w['result']['mean_g']['decimal']),Decimal('250'));self.assertEqual(Decimal(w['result']['biomass_kg']['decimal']),Decimal('250'))
        water=self.write(8,'water-readings/',installation=n,occurred_at=self.at(45,1),temperature_c='26.0000')
        self.assertIsNone(water['ph']);self.assertIsNone(water['oxygen_mg_l']);self.assertFalse(water['result']['has_alert'])
        purchase=self.write(9,'feed-purchases/',new_product={'id':str(uuid.uuid4()),'name':'Aliment fictif 1'},new_category='Aliments',quantity_kg='50.000',amount='100.00',unit='kg',occurred_at=self.at(1,1))
        product=SupplyProduct.objects.get(pk=purchase['product']);self.assertEqual(feed_balance(product),Decimal('50'))
        dist=self.write(10,'feed-distributions/',product=str(product.pk),installation=n,cohort=c,quantity_kg='3.000',unit='kg',occurred_at=self.at(46))
        self.assertIsNone(dist['leftovers_kg']);self.assertEqual(feed_balance(product),Decimal('47'));self.assertEqual(Depense.objects.count(),1)
        self.write(11,'mortalities/',installation=n,cohort=c,quantity=10,occurred_at=self.at(47),reason='Décès réellement comptés');self.state(990,'known mortality once')
        pending=self.write(11,'mortalities/',installation=n,quantity=5,occurred_at=self.at(48),reason='Décès comptés, attribution en attente')
        self.state(985,'unassigned mortality reduces physical total once')
        st=self.read('stock-state/')
        inv=self.write(12,'inventories/',installation=n,observed_count=435,expected_revision=st['business_revision'],occurred_at=self.at(49),reason='Comptage réel égal au physique déjà diminué')
        self.write(12,'loss-reconciliations/',pending=pending['fact']['id'],allocations=[{'cohort':c,'quantity':5}],occurred_at=self.at(49,1),reason='Origine identifiée et vérifiée')
        self.assertEqual(self.state(985,'inventory and reconciliation without second deduction')['unassigned_losses'],0)
        obs=self.write(13,'health-observations/',installation=n,cohort=c,occurred_at=self.at(50),symptoms='Observation fictive',behavior='Contrôle de comportement')
        med=self.write(14,'health-products/',id=str(uuid.uuid4()),name='Produit fictif de qualification',type='TREATMENT',unit='mL')
        self.write(14,'health-treatments/',installation=n,cohort=c,sanitary_product=med['id'],observation=obs['id'],quantity='1.000000',unit='mL',administration_status='ADMINISTERED',occurred_at=self.at(50,1),withdrawal_days=1,withdrawal_source='Document FICTIF réservé au test, valeur non recommandée',reason='Administration synthétique',ongoing=False)
        self.assertEqual(Depense.objects.count(),1)
        g=self.write(15,'brood-groups/',id=str(uuid.uuid4()),name='Sous-groupe déclaré sans prélèvement de stock',species=sp,installation=n,cohort=c,males=20,females=40,occurred_at=self.at(51))
        rep=self.write(16,'reproductions/',id=str(uuid.uuid4()),group=g['id'],installation=n,occurred_at=self.at(52))
        spawn=self.write(16,'breeding-facts/',reproduction=rep['id'],kind='SPAWN',installation=n,occurred_at=self.at(52,1),quality='UNKNOWN')
        inc=self.write(16,'breeding-facts/',reproduction=rep['id'],kind='INCUBATION',parent=spawn['id'],installation=nursery,occurred_at=self.at(52,2),quality='ESTIMATED',quantity=150)
        hatch=self.write(16,'breeding-facts/',reproduction=rep['id'],kind='HATCH',parent=inc['id'],installation=nursery,occurred_at=self.at(55),quality='ESTIMATED',quantity=120)
        self.state(985,'estimated hatch is not live fish')
        self.write(16,'hatch-counts/',hatch=hatch['id'],installation=nursery,quantity=100,actually_counted=True,occurred_at=self.at(56),reason='Comptage physique des alevins')
        fry=self.write(17,'fry-placements/',hatch=hatch['id'],installation=nursery,quantity=100,actually_counted=True,id=str(uuid.uuid4()),code='ALEVINS',name='Alevins séparés du grossissement',occurred_at=self.at(60),reason='Mise en élevage réelle')
        self.state(1085,'exactly 100 new confirmed fry')
        customer=self.write(19,'commerce-clients/',customer={'nom':'Client fictif global'},occurred_at=self.at(85))['id']
        def harvest(day,count,mass,destination):
            at=self.at(day);args={'cohort':c,'installation':n,'live_count':count,'counted':True,'mass_kg':mass,'mass_quality':'MEASURED','stock_unit':'KG','destination':destination,'occurred_at':at,'review':self.review(at,c,n)}
            if destination=='DIRECT':args['direct_sale']={'client':customer,'unit_price':'3.00'}
            else:args.update(storage_place='Glacière fictive',storage_mode='Froid déclaré')
            return self.write(18 if destination=='DIRECT' else 20,'harvests/',**args)
        direct=harvest(86,100,'25.000000','DIRECT');self.state(985,'direct harvest once');self.assertEqual(Vente.objects.count(),1)
        stored=harvest(87,200,'50.000000','STOCK');self.state(785,'stock harvest once')
        for minute,q,count in [(1,'10.000000',40),(2,'15.000000',60)]:
            at=self.at(87,minute);self.write(21,'harvest-sales/',harvest=stored['id'],quantity=q,count=count,price={'client':customer,'unit_price':'3.01'},occurred_at=at,review=self.review(at,harvest=stored['id']))
        self.write(22,'postharvest-losses/',harvest=stored['id'],quantity='5.000000',count=20,occurred_at=self.at(87,3),reason='Perte réellement mesurée pendant conservation')
        remaining=harvested_balance(Harvest.objects.get(pk=stored['id']));self.assertEqual(remaining['available_quantity'],'20.000000');self.assertEqual(remaining['available_count'],80);self.state(785,'postharvest sales and losses do not debit living stock')
        sales=list(Vente.objects.order_by('pk'));self.assertEqual([x.montant_total for x in sales],[Decimal('75'),Decimal('30.10'),Decimal('45.15')])
        invoice=self.read('commerce-invoices/'+str(sales[0].pk)+'/');self.steps.append({'step':23,'invoice':invoice})
        for step,amount in [(24,'20.00'),(25,'55.00')]:self.write(step,'commerce-payments/',sale=sales[0].pk,amount=amount,occurred_at=self.at(88))
        self.assertEqual(self.read('commerce-invoices/'+str(sales[0].pk)+'/')['reste'],'0.00')
        # Existing terrestrial ledger and explicit mixed payment, never a fake aquatic Lot.
        lot=Lot.objects.create(exploitation=self.farm,espece=Espece.objects.create(exploitation=self.farm,nom='Poulets terrestres fictifs'),nom='Vrai lot terrestre distinct',date_debut=self.base.date())
        land=Vente.objects.create(lot=lot,client_id=customer,quantite=1,prix_unitaire='30.00',date=(self.base+timezone.timedelta(days=88)).date())
        mixed=Payment.objects.create(exploitation=self.farm,client_id=customer,montant='50.00',date=(self.base+timezone.timedelta(days=88)).date())
        Lettrage.objects.create(payment=mixed,vente=land,montant='30.00');Lettrage.objects.create(payment=mixed,vente=sales[1],montant='20.00')
        self.write(27,'finance-assignments/',expense=purchase['expense_id'],expected_version=0,scope='AQUATIC',reason='Achat réservé à la pisciculture dans ce jeu fictif')
        day=(self.base+timezone.timedelta(days=88)).date().isoformat()
        for scope,amount,extra in [('COHORT','20.00',{'cohort':c}),('INSTALLATION','10.00',{'installation':n}),('AQUATIC','15.00',{}),('FARM','30.00',{})]:
            self.write(27,'finance-expenses/',new_category='Charge '+scope,amount=amount,date=day,scope=scope,reason='Coût réel explicite fictif',**extra)
        totals=self.read('finance-summary/',**{'from':self.base.date().isoformat(),'to':timezone.localdate().isoformat()})
        for key,value in {'billed_revenue':'150.25','received_payments':'95.00','client_balance_as_of':'55.25','aquatic_expenses':'145.00','farm_common_expenses':'30.00','estimated_result':'5.25'}.items():self.assertEqual(totals[key],value,(key,totals))
        self.steps.append({'step':26,'aquatic_totals':totals,'terrestrial_billed':'30.00','terrestrial_allocated':'30.00'})
        before=[m.objects.count() for m in (Event,HealthRecord,SupplyMovement,Harvest,Vente,Depense,Payment)]
        task=self.write(28,'agenda/',title='Contrôle eau déclaré après acte réel',kind='WATER',date=timezone.localdate().isoformat(),installation=n,cohort=c)
        self.write(28,'agenda-status/',target=task['id'],expected_version=task['version'],status='DONE',reason='Acte existant vérifié',actual_kind='WATER',actual_id=water['id'])
        self.assertEqual(before,[m.objects.count() for m in (Event,HealthRecord,SupplyMovement,Harvest,Vente,Depense,Payment)])
        self.assertTrue(self.read('agenda-history/',target=task['id'])['results']);self.assertTrue(self.read('stock-history/')['results']);self.assertTrue(self.read('events/')['results'])
        self.assertEqual(Depense.objects.count(),5);self.assertEqual(Vente.objects.count(),4);self.assertEqual(feed_balance(product),Decimal('47'))
        self.state(785,'final conservation 1000 -15 +100 -300 =785')
