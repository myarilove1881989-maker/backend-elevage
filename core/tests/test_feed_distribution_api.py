from datetime import date
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import ConsommationAliment, Depense, Espece, Lot, User


class FeedDistributionApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="eleveur-alimentation")
        self.other = User.objects.create_user(username="autre-alimentation")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        espece = Espece.objects.create(nom="Poulet", exploitation=self.user.exploitation)
        self.chair = Lot.objects.create(
            nom="Ancien lot chair", espece=espece,
            exploitation=self.user.exploitation, date_debut=date(2026, 1, 1),
        )
        self.oeufs = Lot.objects.create(
            nom="Lot oeufs", espece=espece, exploitation=self.user.exploitation,
            date_debut=date(2026, 1, 1), type_production="OEUFS",
        )
        autre_espece = Espece.objects.create(
            nom="Poulet", exploitation=self.other.exploitation,
        )
        self.other_lot = Lot.objects.create(
            nom="Lot prive", espece=autre_espece, exploitation=self.other.exploitation,
            date_debut=date(2026, 1, 1),
        )

    def payload(self, lot, quantity="25.000", at=None):
        return {
            "lot": lot.pk, "aliment": "Croissance", "quantite_kg": quantity,
            "distribution_at": (at or timezone.now()).isoformat(),
        }

    def test_two_distributions_in_a_day_and_tenant_history(self):
        morning = timezone.now().replace(hour=8, minute=30, second=0, microsecond=0)
        evening = morning.replace(hour=17, minute=45)
        first = self.client.post(
            "/api/alimentation/distributions/",
            self.payload(self.chair, "25.000", morning), format="json",
        )
        second = self.client.post(
            "/api/alimentation/distributions/",
            self.payload(self.chair, "10.500", evening), format="json",
        )
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(second.status_code, 201, second.data)
        self.assertEqual(first.data["date"], timezone.localtime(morning).date().isoformat())
        self.assertEqual(ConsommationAliment.objects.filter(lot=self.chair).count(), 2)
        self.assertEqual(Depense.objects.count(), 0)

        history = self.client.get(
            f"/api/alimentation/distributions/?lot={self.chair.pk}"
        )
        self.assertEqual(history.status_code, 200, history.data)
        self.assertEqual(len(history.data), 2)
        self.assertEqual(history.data[0]["aliment"], "Croissance")
        self.assertEqual(
            sum(Decimal(item["quantite_kg"]) for item in history.data),
            Decimal("35.500"),
        )

    def test_eggs_and_legacy_url_still_work(self):
        response = self.client.post(
            "/api/oeufs/alimentation/",
            {"lot": self.oeufs.pk, "date": "2026-09-28", "quantite_kg": "5.000"},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["aliment"], "Aliment")
        self.assertEqual(response.data["date"], "2026-09-28")
        self.assertEqual(
            self.client.get(f"/api/alimentation/distributions/?lot={self.oeufs.pk}").status_code,
            200,
        )

    def test_zero_negative_missing_name_and_invalid_date_rejected(self):
        for quantity in ("0", "-0.100"):
            response = self.client.post(
                "/api/alimentation/distributions/",
                self.payload(self.chair, quantity), format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        for invalid in ({"aliment": "   "}, {"distribution_at": "not-a-date"}):
            response = self.client.post(
                "/api/alimentation/distributions/",
                {**self.payload(self.chair), **invalid}, format="json",
            )
            self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(ConsommationAliment.objects.count(), 0)

    def test_other_tenant_cannot_create_read_change_or_delete(self):
        private = ConsommationAliment.objects.create(
            lot=self.other_lot, exploitation=self.other.exploitation,
            date=timezone.localdate(), quantite_kg=Decimal("12.000"),
        )
        created = self.client.post(
            "/api/alimentation/distributions/",
            self.payload(self.other_lot), format="json",
        )
        self.assertEqual(created.status_code, 400)
        self.assertEqual(
            self.client.get(
                f"/api/alimentation/distributions/?lot={self.other_lot.pk}"
            ).status_code, 404,
        )
        path = f"/api/alimentation/distributions/{private.pk}/"
        self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(self.client.patch(path, {"quantite_kg": "2"}, format="json").status_code, 404)
        self.assertEqual(self.client.delete(path).status_code, 404)
        self.assertEqual(list(self.client.get("/api/alimentation/distributions/").data), [])

    def test_correction_and_delete_stay_in_lot_and_tenant(self):
        created = self.client.post(
            "/api/alimentation/distributions/",
            self.payload(self.chair), format="json",
        )
        self.assertEqual(created.status_code, 201)
        path = f"/api/alimentation/distributions/{created.data['id']}/"
        correction = self.client.patch(
            path, {"quantite_kg": "26.000", "note": "Correction"}, format="json",
        )
        self.assertEqual(correction.status_code, 200, correction.data)
        self.assertEqual(correction.data["quantite_kg"], "26.000")
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(ConsommationAliment.objects.count(), 0)
