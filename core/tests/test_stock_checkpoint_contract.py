"""Existing receipt contract: a snapshot is current, confirmation stays per UUID."""
from django.test import TestCase
from rest_framework.test import APIClient
from core.models import Client, TerrainSubmission, Vente
from core.access_policy import effective_permissions
from core.offline_permissions import contract_for
from core.tests.test_terrain_operations import OperationFixture


class StockCheckpointContractTests(OperationFixture, TestCase):
    def test_partial_status_has_current_stock_but_only_requested_original_confirmation(self):
        movement = self.lot.mouvements.get(type_mouvement='ACHAT')
        movement.quantite = 100
        movement.save()
        self.grant.capabilities['permission_contract'] = contract_for(
            self.grant, effective_permissions(self.member))
        self.grant.save()
        client = Client.objects.create(exploitation=self.farm, nom='Client fictif')
        payload = {'lot_ref': self.lot_ref(), 'client_ref': {'server_id': client.pk},
                   'quantite': 10, 'prix_unitaire': '1.00'}
        first = self.terrain('VENTE_ANIMAUX', payload, 1)
        second = self.terrain('VENTE_ANIMAUX', payload, 2)
        self.assertEqual(self.apply(first)['business_status'], 'CONFIRMED')
        self.assertEqual(self.apply(second)['business_status'], 'CONFIRMED')
        response, _ = self.send(first, purpose='STATUS', client=APIClient())
        self.assertEqual(response.status_code, 200, response.data)
        receipts = response.data['receipts']
        self.assertEqual(len(receipts), 1)
        self.assertEqual(receipts[0]['client_operation_id'], first['client_operation_id'])
        self.assertEqual(receipts[0]['stock_snapshots'][0]['stock'], 80)
        self.assertEqual(receipts[0]['stock_snapshots'][0]['confirmed_business_revision'], 2)
        self.assertEqual(receipts[0]['server_version'], 'farm:1')
        self.assertEqual(receipts[0]['author_user_id'], first['author_user_id'])
        self.assertEqual(Vente.objects.count(), 2)
        self.assertEqual(TerrainSubmission.objects.count(), 2)
        self.apply(first)
        self.assertEqual(Vente.objects.count(), 2)
