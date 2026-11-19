import uuid
from datetime import date,timedelta
from unittest.mock import patch
from django.test import TestCase
from django.db import connection,transaction,DatabaseError
from django.core.exceptions import ValidationError as ModelError
from django.utils import timezone
from rest_framework.test import APIClient
from core.models import User,Lot,Espece,Mouvement,Achat,Depense,Vente,Payment
from core.tests.functional_fixtures import operator_for_tests
from .models import TechnicalParameter,ParameterRule,TechnicalReading,ReproductionCycle,ReproductionEvent,Command
from .services import execute
P='/api/elevage/v1/'
class HusbandryTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='owner-husbandry');self.other=User.objects.create_user(username='other-husbandry')
        self.api=APIClient();self.api.force_authenticate(self.owner)
        self.species=Espece.objects.create(exploitation=self.owner.exploitation,nom='Porc')
        self.lot=Lot.objects.create(exploitation=self.owner.exploitation,espece=self.species,nom='Truies',type_production='REPRODUCTION',date_debut=date(2026,1,1))
        self.initial=timezone.now()-timedelta(days=2)
    def post(self,path,expected=201,body=None,**kw):
        data=body or {'schema_version':1,'operation_uuid':str(uuid.uuid4()),**kw}
        r=self.api.post(P+path,data,format='json');self.assertEqual(r.status_code,expected,getattr(r,'data',None));return r
    def get(self,path,expected=200,**kw):
        r=self.api.get(P+path,kw);self.assertEqual(r.status_code,expected,r.data);return r.data
    def parameter(self,kind='NUMBER',name='Température'):
        return self.post('technical/parameters/',species=self.species.pk,name=name,kind=kind,unit='°C' if kind=='NUMBER' else '').data
    def reading(self,p,v,**extra):return self.post('technical/readings/',lot=self.lot.pk,occurred_at=timezone.now().isoformat(),values=[{'parameter':p['id'],'value':v}],**extra)
    def rule(self,p,expected_version=0,**kw):
        data={'lot':self.lot.pk,'parameter':p['id'],'valid_from':self.initial.isoformat(),'visible':True,'alerts_enabled':True,'lower':'10','upper':'30','expected_version':expected_version,**kw}
        return self.post('technical/rules/',**data)
    def cycle(self,mode='COLLECTIVE',**kw):
        data={'lot':self.lot.pk,'name':'Cycle test','mode':mode,'female_count':3 if mode=='COLLECTIVE' else 1,'started_on':'2026-01-01',**kw}
        return self.post('reproduction/cycles/',**data).data
    def event(self,c,kind,data=None,**kw):return self.post('reproduction/events/',cycle=c['id'],kind=kind,occurred_on='2026-06-01',expected_version=c['version'],data=data or {},**kw)
    def test_read_auth_and_operator_closed_without_expanding_legacy_contract(self):
        self.api.force_authenticate(None);self.get('capabilities/',expected=401)
        operator=operator_for_tests(self.owner,'operator-husbandry',['births.create']);self.api.force_authenticate(operator)
        caps=self.get('capabilities/');self.assertFalse(caps['write']);self.get('technical/parameters/',expected=403,species=self.species.pk)
        self.post('technical/setup/',expected=403,species=self.species.pk)
        contract=self.api.get('/api/me/capabilities/').data['permission_contract'];self.assertTrue(all(not x.startswith(('technical.','reproduction.')) for x in contract['permissions']))
    def test_get_does_not_seed_and_setup_is_explicit_idempotent(self):
        self.assertEqual(self.get('technical/parameters/',species=self.species.pk)['results'],[]);self.assertEqual(TechnicalParameter.objects.count(),0)
        body={'schema_version':1,'operation_uuid':str(uuid.uuid4()),'species':self.species.pk};a=self.post('technical/setup/',body=body);b=self.post('technical/setup/',expected=200,body=body)
        self.assertEqual(a.data,b.data);self.assertEqual(TechnicalParameter.objects.count(),5)
    def test_catalog_all_four_families(self):
        for name in ['Poulet','Bovin','Porc','Lapin']:
            species=Espece.objects.create(exploitation=self.owner.exploitation,nom=name)
            rows=self.post('technical/setup/',species=species.pk).data['parameters'];self.assertEqual(len(rows),5)
            self.assertEqual({x['kind'] for x in rows},{'NUMBER','TEXT'} if name=='Poulet' else {'NUMBER','TEXT','BOOL'})
    def test_zero_false_missing_and_multiple_daily_readings(self):
        n=self.parameter();b=self.parameter('BOOL','Eau');t=self.parameter('TEXT','Ventilation')
        self.assertEqual(self.reading(n,'0').data['values'][0]['value'],'0')
        self.assertIs(self.reading(b,False).data['values'][0]['value'],False)
        self.reading(t,'Naturelle');self.reading(n,'0')
        self.assertEqual(TechnicalReading.objects.count(),4)
        self.post('technical/readings/',expected=400,lot=self.lot.pk,occurred_at=timezone.now().isoformat(),values=[{'parameter':n['id'],'value':None}])
    def test_typed_values_reject_bool_numbers_nan_and_precision(self):
        p=self.parameter()
        for v in [True,'NaN','Infinity','1.0000001',1.5]:self.reading_bad(p,v)
        b=self.parameter('BOOL','Eau');self.reading_bad(b,0);self.reading_bad(b,'false')
    def reading_bad(self,p,v):self.post('technical/readings/',expected=400,lot=self.lot.pk,occurred_at=timezone.now().isoformat(),values=[{'parameter':p['id'],'value':v}])
    def test_partial_bounds_and_alerts_off(self):
        p=self.parameter();self.rule(p,lower=None,upper='20')
        r=self.reading(p,'21');self.assertTrue(r.data['values'][0]['alert'])
        self.rule(p,expected_version=1,valid_from=(self.initial+timedelta(hours=1)).isoformat(),alerts_enabled=False,lower=None,upper=None)
        self.assertFalse(self.reading(p,'100').data['values'][0]['alert'])
    def test_threshold_snapshot_and_backdating(self):
        p=self.parameter();rule=self.rule(p).data
        first=self.reading(p,'35').data;self.assertEqual(first['values'][0]['status'],'HIGH')
        self.rule(p,expected_version=1,valid_from=(self.initial+timedelta(days=1)).isoformat(),upper='40')
        self.assertEqual(TechnicalReading.objects.get(pk=first['id']).values[0]['rule']['upper'],'30.000000')
        old=self.post('technical/readings/',lot=self.lot.pk,occurred_at=(self.initial+timedelta(hours=1)).isoformat(),values=[{'parameter':p['id'],'value':'35'}]).data
        self.assertEqual(old['values'][0]['rule']['id'],rule['id'])
    def test_hide_preserves_history_and_rejects_new_hidden_date(self):
        p=self.parameter();self.reading(p,'5');self.rule(p,visible=False,alerts_enabled=False,lower=None,upper=None)
        self.reading_bad(p,'6');self.assertEqual(len(self.get('technical/readings/',lot=self.lot.pk)['results']),1)
        self.assertEqual(self.api.delete(P+'technical/parameters/').status_code,405)
    def test_rules_conflict_bad_bounds_wrong_type(self):
        p=self.parameter();self.post('technical/rules/',expected=400,lot=self.lot.pk,parameter=p['id'],valid_from=self.initial.isoformat(),visible=True,alerts_enabled=True,lower='30',upper='10',expected_version=0)
        self.rule(p);self.post('technical/rules/',expected=409,lot=self.lot.pk,parameter=p['id'],valid_from=timezone.now().isoformat(),visible=True,alerts_enabled=False,expected_version=0)
        p=self.parameter('TEXT','Sol');self.post('technical/rules/',expected=400,lot=self.lot.pk,parameter=p['id'],valid_from=self.initial.isoformat(),visible=True,alerts_enabled=True,lower='1',expected_version=0)
    def test_wrong_species_foreign_parameter_and_foreign_lot(self):
        p=self.parameter();foreign_species=Espece.objects.create(exploitation=self.other.exploitation,nom='Porc')
        foreign_lot=Lot.objects.create(exploitation=self.other.exploitation,espece=foreign_species,nom='Autre',date_debut=date(2026,1,1))
        self.get('technical/readings/',expected=404,lot=foreign_lot.pk)
        foreign=TechnicalParameter.objects.create(exploitation=self.other.exploitation,species=foreign_species,code='foreign',name='X',kind='NUMBER')
        self.post('technical/readings/',expected=404,lot=self.lot.pk,occurred_at=timezone.now().isoformat(),values=[{'parameter':str(foreign.pk),'value':'2'}])
        unsupported=Espece.objects.create(exploitation=self.owner.exploitation,nom='Dinde');self.post('technical/setup/',expected=400,species=unsupported.pk)
    def test_numeric_evolution_and_pagination(self):
        p=self.parameter();self.reading(p,'1');self.reading(p,'2')
        first=self.get('technical/evolution/',lot=self.lot.pk,parameter=p['id'],limit=1);self.assertEqual(len(first['results']),1);self.assertIsNotNone(first['next_cursor'])
        second=self.get('technical/evolution/',lot=self.lot.pk,parameter=p['id'],limit=1,cursor=first['next_cursor']);self.assertNotEqual(first['results'][0]['id'],second['results'][0]['id'])
    def test_invalid_envelope_datetime_empty_and_duplicate_values(self):
        p=self.parameter()
        self.post('technical/readings/',expected=400,lot=self.lot.pk,occurred_at='2026-01-01T12:00:00',values=[{'parameter':p['id'],'value':'2'}])
        self.post('technical/readings/',expected=400,lot=self.lot.pk,occurred_at=timezone.now().isoformat(),values=[])
        self.post('technical/readings/',expected=400,lot=self.lot.pk,occurred_at=timezone.now().isoformat(),values=[{'parameter':p['id'],'value':'1'}]*2)
        self.post('technical/setup/',expected=400,body={'schema_version':True,'operation_uuid':str(uuid.uuid4()),'species':self.species.pk})
    def test_operation_replay_conflict_and_revocation(self):
        p=self.parameter();body={'schema_version':1,'operation_uuid':str(uuid.uuid4()),'lot':self.lot.pk,'occurred_at':timezone.now().isoformat(),'values':[{'parameter':p['id'],'value':'1'}]}
        a=self.post('technical/readings/',body=body);b=self.post('technical/readings/',expected=200,body=body);self.assertEqual(a.data,b.data)
        body['values'][0]['value']='2';self.post('technical/readings/',expected=409,body=body);self.assertEqual(TechnicalReading.objects.count(),1)
        self.owner.is_active=False;self.owner.save();self.post('technical/readings/',expected=403,body=body)
    def test_living_birth_creates_only_child_stock_and_no_finance(self):
        c=self.cycle();r=self.event(c,'BIRTH',{'born_alive':10,'stillborn':2,'litters':3,'new_lot_name':'Jeunes'}).data
        m=Mouvement.objects.get(pk=r['event']['movement']);self.assertEqual(m.lot_origine_id,self.lot.pk);self.assertEqual((m.quantite,m.mort_nes,m.lot.stock,self.lot.stock),(10,2,10,0))
        self.assertEqual(sum(x.objects.count() for x in [Achat,Depense,Vente,Payment]),0)
    def test_stillbirth_without_child_or_movement(self):
        c=self.cycle();r=self.event(c,'BIRTH',{'born_alive':0,'stillborn':4,'litters':2}).data
        self.assertIsNone(r['event']['movement']);self.assertEqual((Lot.objects.count(),Mouvement.objects.count()),(1,0));self.assertEqual(r['cycle']['totals']['stillborn'],4)
    def test_weaning_never_changes_stock_or_creates_movement(self):
        c=self.cycle();r=self.event(c,'BIRTH',{'born_alive':10,'stillborn':0,'litters':2,'new_lot_name':'Jeunes'}).data;c=r['cycle'];before=list(Mouvement.objects.values_list('id','quantite_signee'))
        self.event(c,'WEAN',{'count':8});self.assertEqual(before,list(Mouvement.objects.values_list('id','quantite_signee')));self.assertEqual(Lot.objects.exclude(pk=self.lot.pk).get().stock,10)
    def test_cattle_direct_birth_and_identity_reused_across_cycles(self):
        self.species.nom='Bovin';self.species.save();i=self.post('reproduction/identities/',species=self.species.pk,label='Vache 12').data
        c=self.cycle('INDIVIDUAL',identity=i['id']);r=self.event(c,'BIRTH',{'born_alive':1,'stillborn':0,'new_lot_name':'Veau'}).data
        self.event(r['cycle'],'CLOSE',{'reason':'Cycle terminé'});other=self.cycle('INDIVIDUAL',identity=i['id']);self.assertNotEqual(c['id'],other['id'])
    def test_species_events_nest_optional_and_individual_collective(self):
        for name in ['Porc','Bovin','Lapin']:
            self.species.nom=name;self.species.save();c=self.cycle()
            for kind,data in [('MATING',{'method':'ARTIFICIAL','male_origin':'Origine déclarée'}),('GESTATION',{'result':'UNDETERMINED','method':'Observation'})]:c=self.event(c,kind,data).data['cycle']
            if name=='Lapin':c=self.event(c,'NEST',{'state':'Prêt'}).data['cycle']
            c=self.event(c,'BIRTH',{'born_alive':2,'stillborn':1,'litters':1,'new_lot_name':'Jeunes '+name}).data['cycle']
            c=self.event(c,'WEAN',{'count':2}).data['cycle'];self.event(c,'CLOSE',{'reason':'Terminé'})
    def test_close_without_birth_and_late_event_refused(self):
        c=self.cycle();r=self.event(c,'CLOSE',{'reason':'Aucune naissance'}).data
        self.post('reproduction/events/',expected=409,cycle=c['id'],kind='WEAN',occurred_on='2026-06-01',expected_version=r['cycle']['version'],data={'count':0})
        self.assertEqual(Mouvement.objects.count(),0)
    def test_foreign_cycle_identity_and_stale_version(self):
        c=self.cycle();self.api.force_authenticate(self.other);self.get('reproduction/events/',expected=404,cycle=c['id']);self.post('reproduction/events/',expected=404,cycle=c['id'],kind='WEAN',occurred_on='2026-06-01',expected_version=1,data={'count':1})
        self.api.force_authenticate(self.owner);self.event(c,'GESTATION',{'result':'NEGATIVE'});self.event(c,'WEAN',{'count':0},expected=409)
    def test_replay_birth_is_one_event_and_one_stock_credit(self):
        c=self.cycle();body={'schema_version':1,'operation_uuid':str(uuid.uuid4()),'cycle':c['id'],'kind':'BIRTH','occurred_on':'2026-06-01','expected_version':1,'data':{'born_alive':10,'stillborn':2,'litters':3,'new_lot_name':'Jeunes'}}
        a=self.post('reproduction/events/',body=body);b=self.post('reproduction/events/',expected=200,body=body);self.assertEqual(a.data,b.data);self.assertEqual(Mouvement.objects.count(),1)
    def test_existing_birth_link_does_not_credit_again(self):
        from core.births import create_birth
        child,m=create_birth(self.owner,self.lot,{'nom_nouveau_lot':'Déjà né','type_production':'CHAIR','date':date(2026,6,1),'total_naissances':12,'mort_nes':2})
        c=self.cycle();self.event(c,'BIRTH',{'born_alive':10,'stillborn':2,'litters':2,'existing_movement':m.pk});self.assertEqual((Mouvement.objects.count(),child.stock),(1,10))
        other=self.cycle();self.event(other,'BIRTH',{'born_alive':10,'stillborn':2,'litters':2,'existing_movement':m.pk},expected=409)
    def test_mortality_is_read_from_existing_child_journal(self):
        c=self.cycle();r=self.event(c,'BIRTH',{'born_alive':10,'stillborn':2,'litters':1,'new_lot_name':'Jeunes'}).data
        m=Mouvement.objects.get(pk=r['event']['movement']);Mouvement.objects.create(lot=m.lot,exploitation=self.owner.exploitation,type_mouvement='MORTALITE',quantite=2,date=date(2026,6,2))
        c=self.get('reproduction/cycles/',lot=self.lot.pk)['results'][0];self.assertEqual(c['totals']['preweaning_mortality'],2)
    def test_event_failure_rolls_back_birth_and_command(self):
        c=self.cycle();before=Command.objects.count()
        with patch('husbandry.models.ReproductionEvent.save',side_effect=RuntimeError('synthetic-failure')):
            with self.assertRaises(RuntimeError):self.event(c,'BIRTH',{'born_alive':2,'stillborn':0,'litters':1,'new_lot_name':'Rollback'})
        self.assertEqual((Lot.objects.count(),Mouvement.objects.count(),Command.objects.count()),(1,0,before))
    def test_immutable_reading_orm_and_sql(self):
        p=self.parameter();row=self.reading(p,'1').data
        with self.assertRaises(ModelError):TechnicalReading.objects.filter(pk=row['id']).update(note='Changed')
        if connection.vendor=='postgresql':
            with self.assertRaises(DatabaseError),transaction.atomic():
                with connection.cursor() as c:c.execute('UPDATE husbandry_technicalreading SET note=%s WHERE id=%s',['Changed',row['id']])
    def test_closed_lot_refuses_new_observations(self):
        p=self.parameter();self.lot.date_fin=date(2026,2,1);self.lot.save();self.reading_bad(p,'1');self.get('technical/readings/',lot=self.lot.pk)

    def test_legacy_delete_linked_birth_returns_conflict_and_preserves_stock(self):
        c=self.cycle();response=self.event(c,'BIRTH',{'born_alive':3,'stillborn':0,'litters':1,'new_lot_name':'Child'})
        movement=response.data['event']['movement'];before=Mouvement.objects.count()
        self.api.force_authenticate(operator_for_tests(self.owner,'synthetic-birth-delete',['births.delete']))
        result=self.api.delete(f'/api/mouvements/delete/{movement}/')
        self.assertEqual(result.status_code,409,result.data);self.assertEqual(Mouvement.objects.count(),before)
        self.assertEqual(ReproductionEvent.objects.count(),1)

    def test_legacy_purchase_delete_preserves_technical_history_and_stock(self):
        from decimal import Decimal
        purchase=Achat.objects.create(exploitation=self.owner.exploitation,lot=self.lot,date=date(2026,1,1),quantite=3,prix_total=Decimal('30'),prix_unitaire=Decimal('10'))
        Mouvement.objects.create(exploitation=self.owner.exploitation,lot=self.lot,type_mouvement='ACHAT',quantite=3)
        p=self.parameter();self.reading(p,'24')
        self.api.force_authenticate(operator_for_tests(self.owner,'synthetic-purchase-delete',['purchases.delete']))
        result=self.api.delete(f'/api/achats/delete/{purchase.pk}/')
        self.assertEqual(result.status_code,409,result.data);self.assertTrue(Achat.objects.filter(pk=purchase.pk).exists());self.assertEqual(TechnicalReading.objects.count(),1);self.assertEqual(Mouvement.objects.count(),1)
