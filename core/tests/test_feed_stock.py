from uuid import uuid4
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from core.tests.test_terrain_operations import OperationFixture
from core.models import CategorieDepense,Depense,ConsommationAliment,TerrainSubmission,TerrainOutcome,MembershipPermission
from core.supply_models import SupplyCategoryBinding,SupplyProduct,SupplyOperation,SupplyMovement
from core.supply_services import create_product,stock
from core.offline_permissions import contract_for
from core.access_policy import effective_permissions

class FeedStockTests(OperationFixture,TestCase):
    def setUp(self):
        super().setUp()
        self.category=CategorieDepense.objects.create(exploitation=self.farm,nom='Aliment')
        SupplyCategoryBinding.objects.create(category=self.category,purpose='FEED')
        self.product=create_product(self.owner,self.farm.pk,kind='FEED',name='Croissance')
        self.grant.capabilities.update(feed_stock_contract=1,permission_contract=contract_for(self.grant,effective_permissions(self.member)))
        self.grant.save()
        self.api=APIClient();self.api.force_authenticate(self.owner);self.api.credentials(HTTP_ACCEPT='application/json; elevage-contract=2')
    def buy(self,sequence=1,**supply):
        operation=self.terrain('DEPENSE',{'lot_ref':self.lot_ref(),'categorie_id':self.category.pk,
            'montant':'1000.00','stock_version':1,'feed_supply':{'product':str(self.product.pk),
            'species':self.species.pk,'kg':'500','received_on':str(timezone.localdate()),**supply}},sequence)
        return operation
    def out(self,sequence=2,quantity='80',**extra):
        return self.terrain('ALIMENTATION',{'lot_ref':self.lot_ref(),'stock_version':1,'product':str(self.product.pk),
            'quantite_kg':quantity,'source_lot':None,**extra},sequence)
    def balance(self):return stock(self.owner,self.farm.pk,self.product.pk)['physical']
    def test_offline_purchase_distribution_500_80_and_lost_responses(self):
        purchase=self.buy();first=self.apply(purchase);self.assertEqual(first['business_status'],'CONFIRMED')
        distribution=self.out(supply_parent_uuid=purchase['client_operation_id'])
        distribution['dependencies']=[purchase['client_operation_id']]
        result=self.apply(distribution);self.assertEqual(result['business_status'],'CONFIRMED',result)
        self.apply(purchase);self.apply(distribution)
        self.assertEqual(self.balance(),420);self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(ConsommationAliment.objects.count(),1);self.assertEqual(SupplyMovement.objects.count(),2)
        snap=result['feed_stock_snapshot'];self.assertEqual(snap['coverage'][str(self.device.pk)],[[1,2]])
    def test_child_received_before_purchase_waits_then_confirms(self):
        purchase=self.buy();distribution=self.out(supply_parent_uuid=purchase['client_operation_id'])
        distribution['dependencies']=[purchase['client_operation_id']]
        self.assertNotEqual(self.apply(distribution)['business_status'],'CONFIRMED')
        self.apply(purchase)
        result=self.apply(distribution);self.assertEqual(result['business_status'],'CONFIRMED',result)
        self.assertEqual(self.balance(),420)
    def test_insufficient_and_web_tablet_conflict_keep_original(self):
        self.apply(self.buy())
        body={'stock_version':1,'operation_uuid':str(uuid4()),'lot':self.lot.pk,'product':str(self.product.pk),'quantite_kg':'450'}
        web=self.api.post('/api/alimentation/distributions/',body,format='json');self.assertEqual(web.status_code,201,web.data)
        original=self.out()
        result=self.apply(original);self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(self.balance(),50)
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=original['client_operation_id']).payload,original['payload'])
    def test_reserved_stock_only_its_lot_and_species(self):
        self.apply(self.buy(lot=self.lot.pk))
        self.assertEqual(self.apply(self.out())['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(self.apply(self.out(3,source_lot=self.lot.pk))['business_status'],'CONFIRMED')
        self.assertEqual(self.balance(),420)
        self.assertEqual(self.apply(self.out(4,source_lot=self.lot.pk+10000))['business_status'],'NEEDS_RECONCILIATION')
    def test_revoked_permission_before_sync_refused_even_tolerance(self):
        self.apply(self.buy())
        MembershipPermission.objects.filter(membership=self.member,code='feed.create').delete()
        self.grant.revoked_at=timezone.now();self.grant.revocation_kind='BUSINESS'
        self.grant.business_through_version=self.member.version;self.grant.save()
        result=self.apply(self.out());self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(self.balance(),500)
    def test_lot_closed_while_offline(self):
        self.apply(self.buy());self.lot.date_fin=timezone.localdate();self.lot.save()
        result=self.apply(self.out());self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(result['reason_code'],'LOT_CLOSED');self.assertEqual(self.balance(),500)
    def test_distribution_correction_and_cancellation_are_compensated(self):
        self.apply(self.buy());original=self.out();result=self.apply(original)
        item=ConsommationAliment.objects.get()
        from core.feed_stock import version
        update=self.terrain('ALIMENTATION',{'stock_version':1,'distribution_ref':{'local_uuid':original['local_entity_id']},'quantite_kg':'100','reason':'Quantité corrigée'},3,[original['client_operation_id']])
        update.update(operation_type='UPDATE',expected_server_version=version(item))
        self.assertEqual(self.apply(update)['business_status'],'CONFIRMED')
        self.assertEqual(self.balance(),400)
        cancel=self.terrain('ALIMENTATION',{'stock_version':1,'distribution_ref':{'server_id':item.pk},'reason':'Annulation documentée'},4)
        cancel.update(operation_type='REVERSE',expected_server_version=version(item))
        self.assertEqual(self.apply(cancel)['business_status'],'CONFIRMED')
        self.apply(cancel);self.assertEqual(self.balance(),500)
        self.assertEqual(ConsommationAliment.objects.count(),0);self.assertEqual(ConsommationAliment.history.count(),1)
        self.assertEqual(Depense.objects.get().montant,1000)
    def test_history_and_growth_do_not_deduct_twice(self):
        self.apply(self.buy());self.apply(self.out())
        self.assertEqual(self.api.get('/api/alimentation/distributions/').data[0]['physical_stock']['product'],str(self.product.pk))
        weighing=self.terrain('PESEE',{'lot_ref':self.lot_ref(),'nombre_animaux_peses':2,'poids_total_kg':'5'},3)
        self.assertEqual(self.apply(weighing)['business_status'],'CONFIRMED');self.assertEqual(self.balance(),420)
        self.assertEqual(Depense.objects.count(),1)
    def test_new_local_product_then_dependent_distribution(self):
        product_id=str(uuid4());purchase=self.buy()
        purchase['payload']['feed_supply'].pop('product')
        purchase['payload']['feed_supply']['new_product']={'id':product_id,'name':'Nouveau local'}
        distribution=self.out(product=product_id,supply_parent_uuid=purchase['client_operation_id'])
        distribution['dependencies']=[purchase['client_operation_id']]
        self.apply(purchase)
        self.assertEqual(self.apply(distribution)['business_status'],'CONFIRMED')
        self.assertEqual(stock(self.owner,self.farm.pk,product_id)['physical'],420)
    def test_old_distribution_is_not_guessed_or_retroactively_debited(self):
        self.apply(self.buy())
        old=self.terrain('ALIMENTATION',{'lot_ref':self.lot_ref(),'aliment':'Croissance','quantite_kg':'80'},2)
        self.assertEqual(self.apply(old)['business_status'],'CONFIRMED');self.assertEqual(self.balance(),500)
        self.assertIsNone(self.api.get('/api/alimentation/distributions/').data[0]['physical_stock'])
    def test_feature_not_issued_on_original_grant_cannot_be_forged(self):
        self.grant.capabilities.pop('feed_stock_contract');self.grant.save()
        result=self.apply(self.buy());self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(Depense.objects.count(),0)

    def test_owner_can_close_conflict_without_reapplying_or_erasing_original(self):
        self.apply(self.buy());original=self.out(quantity='900');self.apply(original)
        body={'decision_uuid':str(uuid4()),'expected_decision_version':0,'action':'CANCEL','reason':'Stock insuffisant vérifié : déclaration non appliquée'}
        url=f"/api/offline/reconciliation/{original['client_operation_id']}/"
        forbidden={**body,'action':'APPLY_ORIGINAL'}
        self.assertEqual(self.api.post(url,forbidden,format='json').status_code,403)
        result=self.api.post(url,body,format='json');self.assertEqual(result.status_code,200,result.data)
        self.assertEqual(result.data['receipt']['business_status'],'NOT_APPLIED')
        self.assertEqual(self.balance(),500)
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=original['client_operation_id']).payload,original['payload'])
        self.assertEqual(self.api.post(url,body,format='json').status_code,200)

    def test_explicit_transfer_and_alias_replay_are_single_effect(self):
        self.apply(self.buy())
        body={'operation_uuid':str(uuid4()),'product':str(self.product.pk),'species':self.species.pk,
            'source_lot':None,'target_lot':self.lot.pk,'quantity':'100','reason':'Réservation explicite du lot'}
        first=self.api.post('/api/feed-transfers/',body,format='json');self.assertEqual(first.status_code,200,first.data)
        self.assertEqual(self.api.post('/api/feed-transfers/',body,format='json').status_code,200)
        distribution={'stock_version':1,'operation_uuid':str(uuid4()),'lot':self.lot.pk,'product':str(self.product.pk),'source_lot':self.lot.pk,'quantite_kg':'80'}
        self.assertEqual(self.api.post('/api/alimentation/distributions/',distribution,format='json').status_code,201)
        self.assertEqual(self.api.post('/api/oeufs/alimentation/',distribution,format='json').status_code,201)
        self.assertEqual(ConsommationAliment.objects.count(),1);self.assertEqual(self.balance(),420)
        self.assertEqual(stock(self.owner,self.farm.pk,self.product.pk,lot_id=self.lot.pk)['physical'],20)

    def test_distribution_field_capacity_is_validated_before_database_write(self):
        self.apply(self.buy(kg='10000001'))
        result=self.apply(self.out(quantity='10000000'))
        self.assertEqual(result['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(ConsommationAliment.objects.count(),0)

    def test_http_cancel_lost_response_replays_historical_result_without_second_effect(self):
        self.apply(self.buy())
        create={'stock_version':1,'operation_uuid':str(uuid4()),'lot':self.lot.pk,
            'product':str(self.product.pk),'quantite_kg':'80'}
        response=self.api.post('/api/alimentation/distributions/',create,format='json')
        self.assertEqual(response.status_code,201,response.data)
        body={'operation_uuid':str(uuid4()),'expected_version':response.data['physical_stock']['distribution_version'],
            'reason':'Annulation Web vérifiée','confirm':True}
        url=f"/api/alimentation/distributions/{response.data['id']}/"
        first=self.api.delete(url,body,format='json');self.assertEqual(first.status_code,200,first.data)
        replay=self.api.delete(url,body,format='json');self.assertEqual(replay.status_code,200,replay.data)
        self.assertEqual(first.data,replay.data);self.assertEqual(self.balance(),500)
        self.assertEqual(SupplyOperation.objects.filter(kind='FEED_DIST_CANCEL').count(),1)
        self.assertEqual(ConsommationAliment.history.count(),1)
        changed=self.api.delete(url,{**body,'reason':'Autre commande'},format='json')
        self.assertEqual(changed.status_code,400);self.assertEqual(self.balance(),500)
