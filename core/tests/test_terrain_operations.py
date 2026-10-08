import copy
import threading
from unittest import skipUnless
from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from core.models import (User, Espece, Lot, Mouvement, Achat, Depense, CategorieDepense,
    ConsommationAliment, PeseeProduction, CollecteOeufs, MouvementOeufs,
    TerrainSubmission, TerrainEntityMapping, AuditEvent)
from core.tests.test_terrain_transport import TransportFixture


class OperationFixture(TransportFixture):
    def setUp(self):
        super().setUp()
        self.species=Espece.objects.create(exploitation=self.farm, nom='Espèce test')
        self.lot=Lot.objects.create(exploitation=self.farm, espece=self.species, nom='Parent test',
            date_debut=timezone.localdate())
        Mouvement.objects.create(lot=self.lot, exploitation=self.farm, type_mouvement='ACHAT', quantite=20)

    def terrain(self, kind, payload, sequence=1, dependencies=()):
        operation=self.operation(sequence)
        operation.update(entity_type=kind, payload=payload, dependencies=list(dependencies))
        return operation

    def lot_ref(self):
        return {'server_id':self.lot.pk}

    def apply(self, operation):
        response,_=self.send(operation)
        self.assertEqual(response.status_code,200,response.data)
        return response.data['receipts'][0]


class TerrainOperationsTests(OperationFixture,TestCase):
    def test_purchase_creates_provisional_lot_mapping_then_dependent_mortality(self):
        purchase=self.terrain('ACHAT',{'nom_lot':'Lot acheté','espece':self.species.pk,
            'quantite':10,'prix_total':'1250.00','prix_unitaire':'125.00'})
        receipt=self.apply(purchase)
        self.assertEqual(receipt['business_status'],'CONFIRMED')
        lot=Achat.objects.get().lot
        self.assertEqual(lot.stock,10)
        self.assertEqual(TerrainEntityMapping.objects.get(entity_type='LOT').server_entity_id,lot.pk)
        removal=self.terrain('MORTALITE',{'lot_ref':{'local_uuid':purchase['local_entity_id']},'quantite':2},2,
            [purchase['client_operation_id']])
        receipt=self.apply(removal)
        self.assertEqual(receipt['business_status'],'CONFIRMED')
        self.assertEqual(receipt['stock_snapshots'][0]['stock'],8)
        self.assertEqual(receipt['stock_snapshots'][0]['confirmed_business_revision'],2)
        self.apply(purchase)
        self.assertEqual(Achat.objects.count(),1)
        self.assertEqual(lot.stock,8)
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_APPLIED').count(),2)

    def test_birth_keeps_parent_unchanged_and_stock_excludes_stillborn(self):
        birth=self.terrain('NAISSANCE',{'lot_ref':self.lot_ref(),'total_naissances':5,'mort_nes':2,'nom_nouveau_lot':'Enfant test'})
        receipt=self.apply(birth)
        self.assertEqual(receipt['business_status'],'CONFIRMED')
        movement=Mouvement.objects.get(type_mouvement='NAISSANCE')
        self.assertEqual(movement.lot_origine,self.lot)
        self.assertEqual(movement.quantite,3)
        self.assertEqual(movement.mort_nes,2)
        self.assertEqual(self.lot.stock,20)
        self.assertEqual(movement.lot.stock,3)
        removal=self.terrain('DON',{'lot_ref':{'local_uuid':birth['local_entity_id']},'quantite':1},2,
            [birth['client_operation_id']])
        self.apply(removal)
        refreshed=self.apply(birth)
        self.assertEqual(refreshed['stock_snapshots'][0]['stock'],2)
        self.assertEqual(refreshed['stock_snapshots'][0]['confirmed_business_revision'],2)
        self.assertEqual(self.lot.stock,20)

    def test_all_stillborn_birth_is_retained_for_review_without_empty_child(self):
        operation=self.terrain('NAISSANCE',{'lot_ref':self.lot_ref(),'total_naissances':2,'mort_nes':2,'nom_nouveau_lot':'Pas de vivants'})
        receipt=self.apply(operation)
        self.assertEqual(receipt['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(Lot.objects.count(),1)
        self.assertEqual(TerrainSubmission.objects.get().payload,operation['payload'])

    def test_purchase_mapping_collision_rolls_back_lot_purchase_and_movement_only(self):
        first=self.terrain('ACHAT',{'nom_lot':'Lot acheté','espece':self.species.pk,
            'quantite':10,'prix_total':'1250.00','prix_unitaire':'125.00'})
        self.apply(first)
        second=self.terrain('ACHAT',dict(first['payload']),2)
        second['local_entity_id']=first['local_entity_id']
        receipt=self.apply(second)
        self.assertEqual(receipt['reason_code'],'LOCAL_ENTITY_ALREADY_MAPPED')
        self.assertEqual(Achat.objects.count(),1)
        self.assertEqual(Lot.objects.count(),2)
        self.assertEqual(Mouvement.objects.filter(type_mouvement='ACHAT').count(),2)
        self.assertEqual(TerrainSubmission.objects.count(),2)
        self.farm.refresh_from_db()
        self.assertEqual(self.farm.business_revision,1)

    def test_removals_are_real_declarations_but_confirmed_stock_never_negative(self):
        for sequence,kind in enumerate(['MORTALITE','DON','VOL'],1):
            self.assertEqual(self.apply(self.terrain(kind,{'lot_ref':self.lot_ref(),'quantite':2},sequence))['business_status'],'CONFIRMED')
        operation=self.terrain('VOL',{'lot_ref':self.lot_ref(),'quantite':15,'note':'Constat terrain'},4)
        receipt=self.apply(operation)
        self.assertEqual(receipt['reason_code'],'STOCK_INSUFFICIENT')
        self.assertEqual(self.lot.stock,14)
        self.assertEqual(TerrainSubmission.objects.count(),4)
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=operation['client_operation_id']).payload,operation['payload'])

    def test_expense_and_feed_use_decimal_and_local_expense_dependency(self):
        category=CategorieDepense.objects.create(exploitation=self.farm,nom='Aliment')
        expense=self.terrain('DEPENSE',{'lot_ref':self.lot_ref(),'categorie_id':category.pk,'montant':'123.45'})
        self.assertEqual(self.apply(expense)['business_status'],'CONFIRMED')
        feed=self.terrain('ALIMENTATION',{'lot_ref':self.lot_ref(),'aliment':'Aliment test',
            'quantite_kg':'1.234','prix_kg':'10.00','depense_ref':{'local_uuid':expense['local_entity_id']}},2,[expense['client_operation_id']])
        self.assertEqual(self.apply(feed)['business_status'],'CONFIRMED')
        consumption=ConsommationAliment.objects.get()
        self.assertEqual(str(consumption.quantite_kg),'1.234')
        self.assertEqual(str(consumption.depense.montant),'123.45')
        self.assertEqual(consumption.created_by,self.jean)
        self.assertEqual(Depense.objects.count(),1)

    def test_weighing_uses_existing_sample_and_production_rules(self):
        valid=self.terrain('PESEE',{'lot_ref':self.lot_ref(),'nombre_animaux_peses':2,'poids_total_kg':'1.234'})
        self.assertEqual(self.apply(valid)['business_status'],'CONFIRMED')
        invalid=self.terrain('PESEE',{'lot_ref':self.lot_ref(),'nombre_animaux_peses':21,'poids_total_kg':'2.500'},2)
        self.assertEqual(self.apply(invalid)['business_status'],'NEEDS_RECONCILIATION')
        self.assertEqual(PeseeProduction.objects.count(),1)
        self.assertEqual(str(PeseeProduction.objects.get().poids_total_kg),'1.234')

    def test_egg_collection_preserves_existing_stock_movement_and_losses(self):
        self.lot.type_production='OEUFS';self.lot.save()
        operation=self.terrain('COLLECTE_OEUFS',{'lot_ref':self.lot_ref(),'nombre_collecte':12,
            'nombre_casses':2,'nombre_declasses':1,'nombre_consommes_donnes':1})
        receipt=self.apply(operation)
        self.assertEqual(receipt['business_status'],'CONFIRMED')
        self.assertEqual(CollecteOeufs.objects.get().nombre_commercialisable,8)
        self.assertEqual(MouvementOeufs.objects.get().quantite_signee,8)
        self.assertEqual(receipt['stock_snapshots'][0]['stock_oeufs'],8)
        self.apply(operation)
        self.assertEqual(MouvementOeufs.objects.count(),1)

    def test_cross_farm_references_never_apply_and_original_is_kept(self):
        other=User.objects.create_user(username='operations-other-farm')
        species=Espece.objects.create(exploitation=other.exploitation,nom='Autre espèce')
        lot=Lot.objects.create(exploitation=other.exploitation,espece=species,nom='Autre lot',date_debut=timezone.localdate())
        operation=self.terrain('VOL',{'lot_ref':{'server_id':lot.pk},'quantite':1})
        self.assertEqual(self.apply(operation)['reason_code'],'REFERENCE_OUTSIDE_FARM')
        purchase=self.terrain('ACHAT',{'nom_lot':'Lot interdit','espece':species.pk,
            'quantite':1,'prix_total':'1.00','prix_unitaire':'1.00'},2)
        self.assertEqual(self.apply(purchase)['reason_code'],'SPECIES_OUTSIDE_FARM')
        self.assertEqual(TerrainSubmission.objects.count(),2)
        self.assertFalse(Achat.objects.exists())


@skipUnless(connection.vendor=='postgresql','Real PostgreSQL stock locks required')
class TerrainConcurrentStockTests(OperationFixture,TransactionTestCase):
    def test_simultaneous_real_removals_keep_two_originals_and_one_nonnegative_stock(self):
        barrier=threading.Barrier(2);results=[];errors=[]
        operations=[self.terrain('VOL',{'lot_ref':self.lot_ref(),'quantite':15},sequence) for sequence in [1,2]]
        def worker(operation):
            connections.close_all()
            try:
                barrier.wait(timeout=10)
                response,_=self.send(copy.deepcopy(operation));results.append(response)
            except Exception as error:errors.append(error)
            finally:connections.close_all()
        threads=[threading.Thread(target=worker,args=(operation,)) for operation in operations]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=20)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertFalse(errors,errors)
        self.assertTrue(all(result.status_code==200 for result in results),results)
        self.assertEqual(TerrainSubmission.objects.count(),2)
        self.assertCountEqual(TerrainSubmission.objects.values_list('outcome__business_status',flat=True),['CONFIRMED','NEEDS_RECONCILIATION'])
        self.assertEqual(self.lot.stock,5)
        self.assertEqual(Mouvement.objects.filter(type_mouvement='VOL').count(),1)
