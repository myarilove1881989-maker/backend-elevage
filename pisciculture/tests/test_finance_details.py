from django.test import TestCase
from django.utils import timezone
from core.models import Vente, Lot, User, ExploitationMembership, MembershipPermission, Payment, Lettrage
from core.audit import AuditedQuerySet
from pisciculture.tests.test_phase3d import PlanningFixture
from pisciculture.tests.test_phase1 import PREFIX

class FinanceDetailTests(PlanningFixture, TestCase):
    def detail(self, **filters):
        return self.api.get(PREFIX+'finance-detail/', {'from':self.begin, 'to':self.end, **filters})
    def ledger(self, **filters):
        return self.api.get(PREFIX+'finance-client-ledger/', {'from':self.begin, 'to':self.end, **filters})
    def test_details_reuse_decimal_costs_and_leave_undefined_investment_null(self):
        self.cost(amount='0.10');self.cost(amount='0.20');self.cost('FARM',amount='99.00')
        h=self.harvest();self.sell(h)
        r=self.detail();self.assertEqual(r.status_code,200,r.data)
        self.assertEqual(r.data['aquatic_expenses'],'0.30');self.assertEqual(r.data['billed_revenue'],'75.00')
        self.assertIsNone(r.data['investment']);self.assertIsNone(r.data['recovery_rate']);self.assertIsNone(r.data['investment_difference'])
        self.assertEqual(r.data['cohorts'][0]['revenue'],'75.00');self.assertIsNone(r.data['cohorts'][0]['investment'])
        self.assertEqual(self.detail(cohort=str(self.b.pk)).data['cohorts'],[])
    def test_old_unpaid_invoice_is_in_client_ledger_even_without_recent_payment(self):
        self.sell(self.harvest());s=Vente.objects.get()
        AuditedQuerySet(Vente).filter(pk=s.pk).update(date=self.today-timezone.timedelta(days=90))
        r=self.ledger();self.assertEqual(r.status_code,200,r.data);self.assertEqual(len(r.data['results']),1)
        self.assertEqual(r.data['results'][0]['balance_as_of'],'75.00');self.assertEqual(self.detail().data['billed_revenue'],'0.00')
    def test_mixed_payment_counts_only_same_client_aquatic_allocation(self):
        self.sell(self.harvest());aq=Vente.objects.get()
        lot=Lot.objects.create(exploitation=self.farm,espece=self.species,nom='Terrestre',date_debut=self.today)
        land=Vente.objects.create(lot=lot,client=self.customer,quantite=1,prix_unitaire='30.00',date=self.today)
        p=Payment.objects.create(exploitation=self.farm,client=self.customer,date=self.today,montant='50.00')
        Lettrage.objects.create(payment=p,vente=aq,montant='20.00');Lettrage.objects.create(payment=p,vente=land,montant='30.00')
        r=self.ledger();self.assertEqual(len(r.data['results']),1);row=r.data['results'][0]
        self.assertEqual(row['amount'],'75.00');self.assertEqual(row['paid_as_of'],'20.00');self.assertEqual(row['balance_as_of'],'55.00')
    def test_permissions_tenant_and_unknown_filters(self):
        self.sell(self.harvest());reader=User.objects.create_user(username='detail-no-finance',exploitation=self.farm)
        membership=ExploitationMembership.objects.get(user=reader);MembershipPermission.objects.create(membership=membership,code='aquatic.read')
        self.api.force_authenticate(reader);self.assertEqual(self.detail().status_code,200);self.assertEqual(self.ledger().status_code,200)
        membership.is_active=False;membership.save();self.assertEqual(self.detail().status_code,403);self.assertEqual(self.ledger().status_code,403)
        other=User.objects.create_user(username='detail-other-farm');self.api.force_authenticate(other)
        # An inactive aquatic activity must not expose the first tenant's invoices.
        for result in [self.detail(),self.ledger()]:
            self.assertIn(result.status_code,[200,403])
            if result.status_code==200:self.assertEqual(result.data.get('results',result.data.get('cohorts')),[])
        self.api.force_authenticate(self.owner);self.assertEqual(self.detail(secret='no').status_code,400)
    def test_client_pagination_preserves_exact_once_and_invalidates_on_revision(self):
        h=self.harvest();self.sell(h,'10.000000');self.sell(h,'10.000000')
        first=self.ledger(limit=1);self.assertEqual(first.status_code,200,first.data);cursor=first.data['next_cursor'];self.assertTrue(cursor)
        second=self.ledger(limit=1,cursor=cursor);self.assertEqual(second.status_code,200,second.data)
        self.assertNotEqual(first.data['results'][0]['sale_id'],second.data['results'][0]['sale_id'])
        self.cost();self.assertEqual(self.ledger(limit=1,cursor=cursor).status_code,400)
