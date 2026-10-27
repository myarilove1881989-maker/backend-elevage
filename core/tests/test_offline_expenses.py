from datetime import timedelta
from decimal import Decimal
from unittest.mock import patch
from django.test import TestCase
from core.models import MembershipPermission, CategorieDepense, Depense, TerrainSubmission, AuditEvent, OfflineAuthorization, Achat
from core.access_policy import effective_permissions
from core.offline_permissions import contract_for
from core.terrain_application import process_pending
from core.tests.test_terrain_operations import OperationFixture


class OfflineExpenseTests(OperationFixture, TestCase):
    def setUp(self):
        super().setUp()
        MembershipPermission.objects.filter(membership=self.member).delete()
        self.category=CategorieDepense.objects.create(exploitation=self.farm,nom='Soins')

    def right(self, code):
        MembershipPermission.objects.get_or_create(membership=self.member,code=code,
            defaults={'created_by':self.owner})

    def fine(self):
        self.grant.expires_at=self.grant.issued_at+timedelta(hours=24)
        self.grant.capabilities['permission_contract']=contract_for(self.grant,effective_permissions(self.member))
        self.grant.save()

    def expense(self, sequence=1, lot_ref=None, dependencies=()):
        return self.terrain('DEPENSE',{'lot_ref':lot_ref or self.lot_ref(),
            'categorie_id':self.category.pk,'montant':'123.45','note':'Soin terrain'},
            sequence,dependencies)

    def test_signed_expense_is_applied_once_with_original_author_and_exact_amount(self):
        self.right('expenses.create');self.fine()
        original=self.expense()
        first=self.apply(original);repeat=self.apply(original)
        self.assertEqual(first['business_status'],'CONFIRMED')
        self.assertEqual(first,repeat)
        expense=Depense.objects.get()
        self.assertEqual(expense.montant,Decimal('123.45'))
        self.assertEqual(expense.created_by_id,self.jean.pk)
        self.assertEqual(expense.lot_id,self.lot.pk)
        self.assertEqual(TerrainSubmission.objects.get().payload,original['payload'])
        self.assertEqual(AuditEvent.objects.filter(action='TERRAIN_APPLIED').count(),1)

    def test_signed_permission_and_current_permission_are_both_required(self):
        self.fine()  # Signed snapshot does not authorize expenses.
        self.right('expenses.create')
        receipt=self.apply(self.expense())
        self.assertEqual(receipt['reason_code'],'OFFLINE_PERMISSION_DENIED')
        self.assertFalse(Depense.objects.exists())

    def test_permission_removed_after_receipt_preserves_expense_without_applying(self):
        self.right('expenses.create');self.fine()
        original=self.expense()
        with patch('core.terrain_views.process_pending'):self.apply(original)
        MembershipPermission.objects.filter(membership=self.member,code='expenses.create').delete()
        process_pending(self.device.pk)
        row=TerrainSubmission.objects.get()
        self.assertEqual(row.outcome.reason_code,'OFFLINE_PERMISSION_DENIED')
        self.assertEqual(row.payload,original['payload'])
        self.assertFalse(Depense.objects.exists())

    def test_legacy_expense_without_right_is_preserved_and_cannot_apply(self):
        original=self.expense()
        receipt=self.apply(original)
        self.assertEqual(receipt['reason_code'],'OFFLINE_PERMISSION_DENIED')
        self.assertEqual(TerrainSubmission.objects.get().payload,original['payload'])
        self.assertFalse(Depense.objects.exists())

    def test_authorized_historical_legacy_expense_still_syncs_without_rewriting_grant(self):
        self.right('expenses.create')
        self.assertEqual(self.apply(self.expense())['business_status'],'CONFIRMED')
        self.grant.refresh_from_db()
        self.assertNotIn('permission_contract',self.grant.capabilities)
        self.assertEqual(Depense.objects.count(),1)

    def test_expense_waits_for_local_purchase_then_applies_once(self):
        self.right('purchases.create');self.right('expenses.create');self.fine()
        purchase=self.terrain('ACHAT',{'nom_lot':'Lot local','espece':self.species.pk,
            'quantite':3,'prix_total':'30.00','prix_unitaire':'10.00'})
        expense=self.expense(2,{'local_uuid':purchase['local_entity_id']},[purchase['client_operation_id']])
        self.assertEqual(self.apply(expense)['business_status'],'WAITING_DEPENDENCY')
        self.apply(purchase)
        self.assertEqual(self.apply(expense)['business_status'],'CONFIRMED')
        self.assertEqual(Depense.objects.count(),1)
        self.assertNotEqual(Depense.objects.get().lot_id,self.lot.pk)

    def test_colleague_purchase_dependency_keeps_both_v2_authors_and_applies_once(self):
        self.right('expenses.create');self.fine()
        paul_member=self.paul.memberships.get(exploitation=self.farm)
        MembershipPermission.objects.create(membership=paul_member,code='purchases.create',created_by=self.owner)
        paul_grant=OfflineAuthorization.objects.create(membership=paul_member,device=self.device,
            rights_version=paul_member.version,write_generation=1,capabilities={'can_create_terrain_operation':True},
            issued_at=self.grant.issued_at,expires_at=self.grant.expires_at)
        paul_grant.capabilities['permission_contract']=contract_for(paul_grant,effective_permissions(paul_member))
        paul_grant.save()
        purchase=self.terrain('ACHAT',{'nom_lot':'Lot local de Paul','espece':self.species.pk,
            'quantite':3,'prix_total':'30.00','prix_unitaire':'10.00'})
        purchase.update(author_user_id=self.paul.pk,author_membership_id=paul_member.pk,
            offline_authorization_id=str(paul_grant.pk))
        expense=self.expense(2,{'local_uuid':purchase['local_entity_id']},[purchase['client_operation_id']])
        self.assertEqual(self.apply(expense)['business_status'],'WAITING_DEPENDENCY')
        self.assertEqual(self.apply(purchase)['business_status'],'CONFIRMED')
        self.assertEqual(self.apply(expense)['business_status'],'CONFIRMED')
        self.apply(purchase);self.apply(expense)
        self.assertEqual(Achat.objects.count(),1);self.assertEqual(Depense.objects.count(),1)
        self.assertEqual(Achat.objects.get().created_by_id,self.paul.pk)
        self.assertEqual(Depense.objects.get().created_by_id,self.jean.pk)
        self.assertEqual(Depense.objects.get().lot_id,Achat.objects.get().lot_id)
        self.assertEqual(TerrainSubmission.objects.get(client_operation_id=expense['client_operation_id']).dependencies,
            [purchase['client_operation_id']])
