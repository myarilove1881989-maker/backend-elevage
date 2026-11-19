import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.test import TransactionTestCase
from django.db import close_old_connections,connection
from rest_framework.test import APIClient
from core.models import User,Espece,Lot,Mouvement
from .models import Command,ReproductionCycle,ReproductionEvent

class ConcurrentCommands(TransactionTestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='synthetic-concurrent-owner')
        s=Espece.objects.create(exploitation=self.owner.exploitation,nom='Porc')
        self.lot=Lot.objects.create(exploitation=self.owner.exploitation,espece=s,nom='Truies',type_production='REPRODUCTION',date_debut='2026-01-01')
    def parallel(self,path,bodies):
        gate=Barrier(2)
        def worker(body):
            close_old_connections()
            try:
                api=APIClient();api.force_authenticate(User.objects.get(pk=self.owner.pk));gate.wait(timeout=15)
                response=api.post('/api/elevage/v1/'+path,body,format='json')
                return response.status_code,response.data
            finally:close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:return list(pool.map(worker,bodies))
    def body(self,**kw):return {'schema_version':1,'operation_uuid':str(uuid.uuid4()),**kw}
    def test_concurrent_identical_request_creates_one_cycle(self):
        self.assertEqual(connection.vendor,'postgresql')
        body=self.body(lot=self.lot.pk,name='Cycle',mode='COLLECTIVE',female_count=3,started_on='2026-01-01')
        results=self.parallel('reproduction/cycles/',[body,body])
        self.assertEqual(sorted(x[0] for x in results),[200,201]);self.assertEqual(results[0][1],results[1][1])
        self.assertEqual(ReproductionCycle.objects.count(),1);self.assertEqual(Command.objects.count(),1)
    def test_concurrent_distinct_events_with_same_version_accept_only_one(self):
        cycle=ReproductionCycle.objects.create(exploitation=self.owner.exploitation,lot=self.lot,name='Cycle',mode='COLLECTIVE',female_count=3,started_on='2026-01-01')
        body=dict(cycle=str(cycle.pk),kind='WEAN',occurred_on='2026-02-01',expected_version=1,data={'count':2})
        results=self.parallel('reproduction/events/',[self.body(**body),self.body(**body)])
        self.assertEqual(sorted(x[0] for x in results),[201,409]);self.assertEqual(ReproductionEvent.objects.count(),1);self.assertEqual(Mouvement.objects.count(),0)
