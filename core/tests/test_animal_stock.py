from datetime import date, datetime, timezone as dt_timezone
from decimal import Decimal
import uuid

from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from core.animal_stock import current_animal_stock
from core.cache_views import lot_stock_queryset, stock_snapshots
from core.egg_services import get_live_birds, get_egg_stock, get_hen_days
from core.models import (Achat, Client, DeviceRegistration, Espece, Lot, MembershipPermission,
                         Mouvement, MouvementOeufs, OfflineAuthorization, TerrainStockAdjustment,
                         TerrainSubmission, User)
from core.serializers import LotSerializer, LotDetailSerializer
from core.utils import get_lot_stock as utility_stock
from core.views import get_lot_stock as view_stock


class AnimalStockTests(TestCase):
    def setUp(self):
        self.owner=User.objects.create_user(username='c111-owner')
        self.farm=self.owner.exploitation
        self.operator=User.objects.create_user(username='c111-operator',exploitation=self.farm)
        self.other=User.objects.create_user(username='c111-other')
        self.species=Espece.objects.create(nom='Synthetic animals',exploitation=self.farm)
        self.lot=self.new_lot('Synthetic lot')
        member=self.operator.memberships.get()
        for code in ['mortality.create','animal_sales.create','weights.create','purchases.create',
                     'births.create','donations.create','thefts.create']:
            MembershipPermission.objects.create(membership=member,code=code)
        self.device=DeviceRegistration.objects.create(exploitation=self.farm,
            installation_uuid=uuid.uuid4(),display_name='Synthetic only',public_key='synthetic')
        self.grant=OfflineAuthorization.objects.create(membership=member,device=self.device,
            capabilities={},rights_version=member.version,write_generation=1,
            expires_at=timezone.now())
        self.api=APIClient();self.api.force_authenticate(self.operator)
        self.sequence=0

    def new_lot(self,name,**kwargs):
        return Lot.objects.create(exploitation=kwargs.pop('exploitation',self.farm),
            espece=self.species,nom=name,date_debut=date(2026,9,1),**kwargs)

    def movement(self,kind,quantity,lot=None,**kwargs):
        lot=lot or self.lot
        return Mouvement.objects.create(lot=lot,exploitation=kwargs.pop('exploitation',lot.exploitation),
            type_mouvement=kind,quantite=quantity,date=kwargs.pop('date',date(2026,9,1)),**kwargs)

    def adjustment(self,quantity,lot=None,kind='ANIMAL',occurred_at=None,exploitation=None):
        lot=lot or self.lot;self.sequence+=1
        row=TerrainSubmission.objects.create(exploitation=self.farm,device=self.device,
            offline_authorization=self.grant,client_operation_id=uuid.uuid4(),
            local_sequence=self.sequence,author_user_id=self.operator.pk,
            author_membership_id=self.grant.membership_id,device_generation=1,
            entity_type='MORTALITE',operation_type='CREATE',payload={'quantite':abs(quantity)},
            business_occurred_at=datetime(2026,9,1,12,tzinfo=dt_timezone.utc),
            local_recorded_at=timezone.now(),declaration_digest='a'*64)
        return TerrainStockAdjustment.objects.create(exploitation=exploitation or lot.exploitation,
            submission=row,decision_uuid=uuid.uuid4(),lot=lot,kind=kind,signed_quantity=quantity,
            occurred_at=occurred_at or timezone.now())

    def assert_readers(self,expected,lot=None):
        lot=lot or self.lot
        with self.assertNumQueries(1):
            self.assertEqual(current_animal_stock(lot),expected)
        self.assertEqual(lot.stock,expected)
        self.assertEqual(view_stock(lot),expected)
        self.assertEqual(utility_stock(lot),expected)
        self.assertEqual(lot_stock_queryset(lot.exploitation_id).get(pk=lot.pk).confirmed_stock,expected)
        self.assertEqual(LotSerializer(lot).data['stock'],expected)
        self.assertEqual(LotDetailSerializer(lot).data['stock'],expected)
        self.assertEqual(stock_snapshots(lot.exploitation_id,[lot.pk])[0]['stock'],expected)
        self.assertEqual(get_live_birds(lot),max(expected,0))

    def test_purchase_birth_and_all_exits_are_counted_once(self):
        purchase=Achat.objects.create(lot=self.lot,exploitation=self.farm,quantite=20,
            prix_total='40',prix_unitaire='2',date=date(2026,9,1))
        self.movement('ACHAT',20)
        self.movement('NAISSANCE',4,mort_nes=2)
        for kind,count in [('VENTE',3),('MORTALITE',2),('DON',1),('VOL',1)]:
            self.movement(kind,count)
        self.assert_readers(17)
        self.assertEqual(purchase.quantite,20)
        self.assertEqual(Mouvement.objects.filter(lot=self.lot).count(),6)

    def test_positive_negative_compensations_and_originals_are_preserved(self):
        original=self.movement('ACHAT',10)
        negative=self.adjustment(-2);positive=self.adjustment(3)
        self.assert_readers(11)
        for _ in range(3): self.assert_readers(11)
        original.refresh_from_db();negative.refresh_from_db();positive.refresh_from_db()
        self.assertEqual(original.quantite_signee,10)
        self.assertEqual((negative.signed_quantity,positive.signed_quantity),(-2,3))
        self.assertEqual(TerrainStockAdjustment.objects.count(),2)

    def test_multiple_lots_and_farms_do_not_share_compensations(self):
        neighbour=self.new_lot('Neighbour');foreign=self.new_lot('Foreign',exploitation=self.other.exploitation)
        self.movement('ACHAT',10);self.movement('ACHAT',20,neighbour);self.movement('ACHAT',30,foreign)
        self.adjustment(-2);self.adjustment(1,neighbour);self.adjustment(-3,foreign)
        self.assert_readers(8);self.assert_readers(21,neighbour);self.assert_readers(27,foreign)
        response=self.api.get('/api/dashboard/')
        self.assertEqual(response.status_code,200,response.data)
        self.assertEqual(response.data['kpis']['stock'],29)
        self.assertEqual(stock_snapshots(self.farm.pk,[foreign.pk]),[])

    def test_null_legacy_movement_is_kept_and_explicit_foreign_tenant_is_excluded(self):
        self.movement('ACHAT',10,exploitation=None)
        self.movement('ACHAT',99,exploitation=self.other.exploitation)
        self.adjustment(-2)
        self.adjustment(100,exploitation=self.other.exploitation)
        self.assert_readers(8)
        self.assertEqual(Mouvement.objects.count(),2)
        self.assertEqual(TerrainStockAdjustment.objects.count(),2)
        detail=self.api.get('/api/stock-detail/',{'lot':self.lot.pk})
        self.assertEqual(detail.data['stock_initial'],10)
        self.assertEqual(detail.data['stock_restant'],8)

    def test_http_purchase_and_birth_create_one_entry_with_only_living_children(self):
        response=self.api.post('/api/achats/create/',{
            'nom_lot':'HTTP purchase','espece':self.species.pk,'quantite':10,
            'prix_total':'20.00','prix_unitaire':'2.00','date':'2026-09-01'},format='json')
        self.assertEqual(response.status_code,201,response.data)
        purchased=Lot.objects.get(pk=response.data['lot_id'])
        self.assert_readers(10,purchased)
        self.assertEqual(Mouvement.objects.filter(lot=purchased).count(),1)
        response=self.api.post('/api/mouvements/create/',{
            'lot':purchased.pk,'type_mouvement':'NAISSANCE','date':'2026-09-02',
            'total_naissances':5,'mort_nes':2,'nom_nouveau_lot':'HTTP children'},format='json')
        self.assertEqual(response.status_code,201,response.data)
        child=Lot.objects.get(pk=response.data['nouveau_lot']['id'])
        self.assert_readers(10,purchased);self.assert_readers(3,child)
        self.assertEqual(child.mouvements.get().mort_nes,2)
        self.assertEqual(Achat.objects.count(),1)

    def test_sale_and_other_exits_are_limited_by_compensated_stock(self):
        self.movement('ACHAT',10);self.adjustment(-2)
        customer=Client.objects.create(nom='Synthetic buyer',exploitation=self.farm)
        for kind in ['VENTE','MORTALITE','DON','VOL']:
            payload={'lot':self.lot.pk,'type_mouvement':kind,'quantite':9,'date':'2026-10-12'}
            if kind=='VENTE':payload.update(client=customer.pk,prix_unitaire='2.00')
            response=self.api.post('/api/mouvements/create/',payload,format='json')
            self.assertEqual(response.status_code,400,response.data)
            self.assertIn('8',str(response.data))
        self.assertEqual(self.lot.mouvements.count(),1)
        self.assert_readers(8)

    def test_empty_terminated_and_negative_anomaly_are_not_rewritten(self):
        self.assert_readers(0)
        self.lot.statut_production='TERMINE';self.lot.save(update_fields=['statut_production'])
        self.movement('ACHAT',10);self.adjustment(-12)
        self.assert_readers(-2)
        self.lot.refresh_from_db();self.assertEqual(self.lot.statut_production,'TERMINE')

    def test_purchase_document_without_movement_is_not_invented(self):
        Achat.objects.create(lot=self.lot,exploitation=self.farm,quantite=10,
            prix_total='20',prix_unitaire='2',date=date(2026,9,1))
        self.assert_readers(0)
        self.assertEqual(Mouvement.objects.count(),0)
        self.assertEqual(Achat.objects.get().quantite,10)

    def test_animal_and_egg_ledgers_are_independent(self):
        self.movement('ACHAT',10);self.adjustment(-2)
        MouvementOeufs.objects.create(lot=self.lot,exploitation=self.farm,
            type_mouvement='PRODUCTION',quantite=100,date=timezone.now())
        self.adjustment(-7,kind='EGG')
        self.assert_readers(8)
        self.assertEqual(get_egg_stock(self.farm,self.lot),93)
        snapshot=stock_snapshots(self.farm.pk,[self.lot.pk])[0]
        self.assertEqual((snapshot['stock'],snapshot['stock_oeufs']),(8,93))

    def test_api_detail_dashboard_breakdown_cache_and_snapshot_agree(self):
        self.movement('ACHAT',10);self.adjustment(-2)
        self.assertEqual(self.api.get('/api/dashboard/').data['kpis']['stock'],8)
        self.assertEqual(self.api.get(f'/api/lots/{self.lot.pk}/').data['stock'],8)
        self.assertEqual(self.api.get('/api/stock-detail/',{'lot':self.lot.pk}).data['stock_restant'],8)
        cache=self.api.get('/api/cache-page/',{'collection':'lots'})
        self.assertEqual(cache.status_code,200,cache.data)
        snapshot=stock_snapshots(self.farm.pk,[self.lot.pk])[0]
        self.assertEqual(cache.data['results'][0],snapshot)
        self.assertEqual(cache.data['results'][0]['stock'],8)

    def test_exit_and_weighing_validation_use_compensated_stock(self):
        self.movement('ACHAT',10);self.adjustment(-2)
        response=self.api.post('/api/mouvements/create/',{
            'lot':self.lot.pk,'type_mouvement':'MORTALITE','quantite':9,'date':'2026-10-12'},format='json')
        self.assertEqual(response.status_code,400,response.data)
        self.assertIn('8',str(response.data))
        response=self.api.post('/api/production/pesees/',{
            'lot':self.lot.pk,'pesee_at':'2026-10-12T12:00:00Z',
            'nombre_animaux_peses':9,'poids_total_kg':'9.000'},format='json')
        self.assertEqual(response.status_code,400,response.data)
        self.assertIn('8',str(response.data))
        self.assert_readers(8)

    def test_canonical_query_cannot_multiply_movements_with_related_joins(self):
        self.movement('ACHAT',10);self.movement('MORTALITE',1)
        self.adjustment(-2);self.adjustment(1)
        from core.animal_stock import animal_stock_expression
        rows=Lot.objects.filter(pk=self.lot.pk).filter(mouvements__quantite__gt=0).annotate(stock_value=animal_stock_expression())
        self.assertEqual([r.stock_value for r in rows],[8,8])
        self.assert_readers(8)

    @override_settings(TIME_ZONE='Europe/Paris')
    def test_historical_hen_days_use_compensation_effect_day_not_original_day(self):
        day=date(2026,9,1)
        Achat.objects.create(lot=self.lot,exploitation=self.farm,quantite=10,
            prix_total='20',prix_unitaire='2',date=day)
        self.movement('ACHAT',10,date=day)
        # 23:30 UTC on Sept 2 is Sept 3 in the business timezone.
        effect=datetime(2026,9,2,23,30,tzinfo=dt_timezone.utc)
        adjustment=self.adjustment(-2,occurred_at=effect)
        self.assertEqual(get_hen_days(self.lot,day,date(2026,9,2)),20)
        self.assertEqual(get_hen_days(self.lot,day,date(2026,9,3)),28)
        self.assert_readers(8)
        adjustment.refresh_from_db()
        self.assertEqual(adjustment.occurred_at,effect)
        self.assertEqual(adjustment.submission.business_occurred_at.date(),day)
        self.assertNotEqual(adjustment.occurred_at,adjustment.submission.local_recorded_at)
