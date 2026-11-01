from django.test import TestCase
from core.tests.test_terrain_operations import OperationFixture
from core.models import CollecteOeufs, MouvementOeufs, TerrainSubmission, MembershipPermission
from core.egg_collection_version import collection_version

class Day1CorrectionsTests(OperationFixture, TestCase):
    def create_collection(self):
        self.lot.type_production='OEUFS';self.lot.save()
        operation=self.terrain('COLLECTE_OEUFS',{'lot_ref':self.lot_ref(),'nombre_collecte':120,'nombre_casses':3})
        self.assertEqual(self.apply(operation)['business_status'],'CONFIRMED')
        return operation, CollecteOeufs.objects.get()

    def correction(self, original, collection, total=110, sequence=2):
        operation=self.terrain('COLLECTE_OEUFS',{'lot_ref':self.lot_ref(),
            'collection_ref':{'local_uuid':original['local_entity_id']},
            'nombre_collecte':total,'nombre_casses':3,'nombre_declasses':0,
            'nombre_consommes_donnes':0,'note':'Correction jour 1',
            'previous_quantity':collection.nombre_commercialisable},sequence,[original['client_operation_id']])
        operation.update(operation_type='UPDATE',expected_server_version=collection_version(collection))
        return operation

    def test_correction_preserves_original_and_one_stock_movement_with_idempotent_retry(self):
        original,collection=self.create_collection()
        correction=self.correction(original,collection)
        receipt=self.apply(correction)
        self.assertEqual(receipt['business_status'],'CONFIRMED',receipt)
        self.assertEqual(receipt['stock_snapshots'][0]['stock_oeufs'],107)
        self.apply(correction)
        self.assertEqual(CollecteOeufs.objects.count(),1)
        self.assertEqual(MouvementOeufs.objects.count(),1)
        self.assertEqual(MouvementOeufs.objects.get().quantite_signee,107)
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=original['client_operation_id']).payload['nombre_collecte'],120)

    def test_stale_version_is_reconciled_without_stock_effect(self):
        original,collection=self.create_collection()
        correction=self.correction(original,collection)
        correction['expected_server_version']='stale'
        receipt=self.apply(correction)
        self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(receipt['reason_code'],'COLLECTION_VERSION_CONFLICT')
        self.assertEqual(MouvementOeufs.objects.get().quantite_signee,117)

    def test_reduction_below_existing_outgoing_stock_rolls_back(self):
        original,collection=self.create_collection()
        MouvementOeufs.objects.create(exploitation=self.farm,lot=self.lot,type_mouvement='VENTE',quantite=100,created_by=self.jean)
        receipt=self.apply(self.correction(original,collection,total=50))
        self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION')
        collection.refresh_from_db();self.assertEqual(collection.nombre_collecte,120)

    def test_create_right_does_not_authorize_correction(self):
        original,collection=self.create_collection()
        MembershipPermission.objects.filter(membership=self.member,code='egg_collections.update').delete()
        receipt=self.apply(self.correction(original,collection))
        self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION')
        collection.refresh_from_db();self.assertEqual(collection.nombre_collecte,120)

    def test_authenticated_online_correction_detects_stale_version(self):
        from rest_framework.test import APIClient
        original,collection=self.create_collection()
        self.farm.offline_policy_enabled=False;self.farm.save()
        client=APIClient();client.force_authenticate(self.jean)
        endpoint=f'/api/oeufs/collectes/{collection.pk}/'
        version=client.get(endpoint).data['collection_version']
        response=client.patch(endpoint,{'nombre_collecte':110,'expected_version':version},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.assertNotEqual(response.data['collection_version'],version)
        response=client.patch(endpoint,{'nombre_collecte':100,'expected_version':version},format='json')
        self.assertEqual(response.status_code,400,response.data)
        collection.refresh_from_db();self.assertEqual(collection.nombre_collecte,110)

    def test_correction_cannot_cross_farm(self):
        from core.models import User, Espece, Lot
        from django.utils import timezone
        original,collection=self.create_collection()
        other=User.objects.create_user(username='day1-other')
        species=Espece.objects.create(exploitation=other.exploitation,nom='Other')
        lot=Lot.objects.create(exploitation=other.exploitation,espece=species,nom='Other',date_debut=timezone.localdate(),type_production='OEUFS')
        foreign=CollecteOeufs.objects.create(exploitation=other.exploitation,lot=lot,nombre_collecte=500)
        operation=self.correction(original,collection)
        operation['payload']['collection_ref']={'server_id':foreign.pk}
        self.assertEqual(self.apply(operation)['business_status'],'NEEDS_RECONCILIATION')
        foreign.refresh_from_db();self.assertEqual(foreign.nombre_collecte,500)
