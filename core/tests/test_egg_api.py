from datetime import date, datetime, time
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient

from core.models import Achat, CollecteOeufs, Espece, Lot, MouvementOeufs, User


class EggCollectionApiTests(TestCase):
    def test_type_production_round_trip_and_legacy_default(self):
        legacy_lot = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Ancien lot",
            date_debut=date(2025, 1, 1),
        )

        list_response = self.client.get("/api/lots/")
        detail_response = self.client.get(f"/api/lots/{self.lot.id}/")

        self.assertEqual(list_response.status_code, 200)
        lots_by_id = {item["id"]: item for item in list_response.data}
        self.assertEqual(lots_by_id[legacy_lot.id]["type_production"], "CHAIR")
        self.assertEqual(lots_by_id[self.lot.id]["type_production"], "OEUFS")
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.data["type_production"], "OEUFS")
        self.assertEqual(detail_response.data["statut_production"], "PONTE")

    def test_all_current_production_type_values_remain_available(self):
        production_types = dict(Lot.TYPE_PRODUCTION_CHOICES)

        self.assertEqual(
            set(production_types),
            {"CHAIR", "OEUFS", "REPRODUCTION", "AUTRE"},
        )
        self.assertEqual(Lot._meta.get_field("type_production").default, "CHAIR")

    def test_laying_rate_uses_hen_days_for_multiday_period(self):
        from datetime import timedelta
        from core.egg_services import get_hen_days
        today = timezone.localdate()
        self.client.post('/api/oeufs/collectes/', self.collection_payload(), format='json')
        start = today - timedelta(days=6)
        self.assertEqual(get_hen_days(self.lot, start, today), 700)
        response = self.client.get(
            f'/api/oeufs/statistiques/?lot={self.lot.pk}&date_debut={start}&date_fin={today}'
        )
        self.assertEqual(response.data['taux_ponte'], 11.43)

    def test_sold_eggs_prevent_collection_reduction_or_deletion(self):
        response = self.client.post('/api/oeufs/collectes/', self.collection_payload(), format='json')
        pk = response.data['id']
        MouvementOeufs.objects.create(
            exploitation=self.user.exploitation, lot=self.lot,
            type_mouvement='VENTE', quantite=60, created_by=self.user,
        )
        self.assertEqual(self.client.patch(
            f'/api/oeufs/collectes/{pk}/', {'nombre_collecte': 20}, format='json'
        ).status_code, 400)
        self.assertEqual(CollecteOeufs.objects.get(pk=pk).nombre_collecte, 80)
        self.assertEqual(self.client.delete(f'/api/oeufs/collectes/{pk}/').status_code, 400)
        self.assertEqual(MouvementOeufs.objects.get(collecte_id=pk).quantite_signee, 70)

    def setUp(self):
        self.user = User.objects.create_user(username="pondeur-api", password="test-pass")
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.espece = Espece.objects.create(
            nom="Poulet",
            exploitation=self.user.exploitation,
        )
        self.lot = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Pondeuses API",
            date_debut=date(2026, 1, 1),
            type_production="OEUFS",
            statut_production="PONTE",
        )
        Achat.objects.create(
            exploitation=self.user.exploitation,
            lot=self.lot,
            quantite=100,
            prix_total=Decimal("100000.00"),
            prix_unitaire=Decimal("1000.00"),
            date=date(2026, 1, 1),
            created_by=self.user,
        )

    def collection_payload(self, **overrides):
        payload = {
            "lot": self.lot.id,
            "collecte_at": timezone.now().isoformat(),
            "nombre_collecte": 80,
            "nombre_casses": 3,
            "nombre_declasses": 2,
            "nombre_consommes_donnes": 5,
            "note": "Collecte du matin",
        }
        payload.update(overrides)
        return payload

    def test_create_collection_creates_exactly_one_stock_entry(self):
        response = self.client.post(
            "/api/oeufs/collectes/",
            self.collection_payload(),
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.data["nombre_commercialisable"], 70)
        collection = CollecteOeufs.objects.get(pk=response.data["id"])
        movement = MouvementOeufs.objects.get(collecte=collection)
        self.assertEqual(movement.quantite_signee, 70)
        self.assertEqual(MouvementOeufs.objects.filter(collecte=collection).count(), 1)

    def test_alveoles_and_remainder_are_canonicalized_without_migration(self):
        collected_at = timezone.now().replace(microsecond=0)
        payload = self.collection_payload()
        payload.pop("nombre_collecte")
        payload.update(collecte_at=collected_at.isoformat(), nombre_alveoles=61,
                       oeufs_restants=17, nombre_casses=12, nombre_declasses=5,
                       nombre_consommes_donnes=10)
        response = self.client.post("/api/oeufs/collectes/", payload, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["nombre_collecte"], 1847)
        self.assertEqual(response.data["nombre_commercialisable"], 1820)
        self.assertEqual(response.data["nombre_alveoles"], 61)
        self.assertEqual(response.data["oeufs_restants"], 17)
        self.assertEqual(MouvementOeufs.objects.get(collecte_id=response.data["id"]).quantite_signee, 1820)
        self.assertEqual(CollecteOeufs.objects.get(pk=response.data["id"]).collecte_at, collected_at)

        updated = self.client.patch(
            f"/api/oeufs/collectes/{response.data['id']}/",
            {"nombre_alveoles": 62, "oeufs_restants": 5}, format="json",
        )
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data["nombre_collecte"], 1865)
        self.assertEqual(MouvementOeufs.objects.get(collecte_id=response.data["id"]).quantite_signee, 1838)

    def test_old_raw_total_is_still_readable_as_alveoles_and_remainder(self):
        response = self.client.post(
            "/api/oeufs/collectes/",
            self.collection_payload(nombre_collecte=70, nombre_casses=0,
                                    nombre_declasses=0, nombre_consommes_donnes=0),
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.data)
        listing = self.client.get(f"/api/oeufs/collectes/?lot={self.lot.pk}")
        self.assertEqual((listing.data[0]["nombre_alveoles"], listing.data[0]["oeufs_restants"]), (2, 10))

    def test_alveole_input_rejects_empty_invalid_and_ambiguous_collectes(self):
        payload = self.collection_payload()
        payload.pop("nombre_collecte")
        for alveoles, restants in [(0, 0), (0, 30), (-1, 2), (0, -1)]:
            response = self.client.post(
                "/api/oeufs/collectes/",
                {**payload, "nombre_alveoles": alveoles, "oeufs_restants": restants},
                format="json",
            )
            self.assertEqual(response.status_code, 400, (alveoles, restants, response.data))
        response = self.client.post("/api/oeufs/collectes/", {
            **payload, "nombre_alveoles": 1, "oeufs_restants": 0,
            "nombre_collecte": 2500,
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_several_collectes_same_day_keep_distinct_times_and_order(self):
        day = timezone.localdate()
        for hour, alveoles, restants in [(8, 20, 12), (13, 18, 4), (17, 22, 19)]:
            payload = self.collection_payload()
            payload.pop("nombre_collecte")
            payload.update(collecte_at=timezone.make_aware(
                datetime.combine(day, time(hour, 10))
            ).isoformat(), nombre_alveoles=alveoles, oeufs_restants=restants)
            response = self.client.post("/api/oeufs/collectes/", payload, format="json")
            self.assertEqual(response.status_code, 201, response.data)
        response = self.client.get(f"/api/oeufs/collectes/?lot={self.lot.id}")
        self.assertEqual([row["nombre_collecte"] for row in response.data], [679, 544, 612])

    def test_patch_collection_updates_existing_stock_entry(self):
        create_response = self.client.post(
            "/api/oeufs/collectes/",
            self.collection_payload(),
            format="json",
        )
        collection_id = create_response.data["id"]
        movement_id = MouvementOeufs.objects.get(collecte_id=collection_id).id

        response = self.client.patch(
            f"/api/oeufs/collectes/{collection_id}/",
            {"nombre_collecte": 90},
            format="json",
        )

        self.assertEqual(response.status_code, 200)
        movement = MouvementOeufs.objects.get(collecte_id=collection_id)
        self.assertEqual(movement.id, movement_id)
        self.assertEqual(movement.quantite_signee, 80)

    def test_delete_collection_removes_automatic_stock_entry(self):
        create_response = self.client.post(
            "/api/oeufs/collectes/",
            self.collection_payload(),
            format="json",
        )
        collection_id = create_response.data["id"]

        response = self.client.delete(f"/api/oeufs/collectes/{collection_id}/")

        self.assertEqual(response.status_code, 204)
        self.assertFalse(CollecteOeufs.objects.filter(id=collection_id).exists())
        self.assertFalse(MouvementOeufs.objects.filter(collecte_id=collection_id).exists())

    def test_rejects_collection_for_another_tenant_lot(self):
        other_user = User.objects.create_user(username="autre-api", password="test-pass")
        response = self.client.post(
            "/api/oeufs/collectes/",
            self.collection_payload(),
            format="json",
        )
        own_collection_id = response.data["id"]

        other_client = APIClient()
        other_client.force_authenticate(other_user)
        detail_response = other_client.get(f"/api/oeufs/collectes/{own_collection_id}/")
        list_response = other_client.get(f"/api/oeufs/collectes/?lot={self.lot.id}")

        self.assertEqual(detail_response.status_code, 404)
        self.assertEqual(list_response.status_code, 404)
        self.assertEqual(other_client.patch(
            f"/api/oeufs/collectes/{own_collection_id}/",
            {"nombre_alveoles": 3, "oeufs_restants": 0}, format="json",
        ).status_code, 404)
        self.assertEqual(other_client.delete(
            f"/api/oeufs/collectes/{own_collection_id}/"
        ).status_code, 404)
        other_payload = self.collection_payload()
        other_payload.pop("nombre_collecte")
        other_payload.update(nombre_alveoles=3, oeufs_restants=0)
        self.assertEqual(other_client.post(
            "/api/oeufs/collectes/", other_payload, format="json",
        ).status_code, 400)

    def test_rejects_collection_for_non_laying_lot(self):
        lot_chair = Lot.objects.create(
            exploitation=self.user.exploitation,
            espece=self.espece,
            nom="Chair API",
            date_debut=date(2026, 1, 1),
        )

        response = self.client.post(
            "/api/oeufs/collectes/",
            self.collection_payload(lot=lot_chair.id),
            format="json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("lot", response.data)

    def test_statistics_use_live_birds_and_traceable_egg_stock(self):
        self.client.post(
            "/api/oeufs/collectes/",
            self.collection_payload(),
            format="json",
        )

        today = timezone.localdate().isoformat()
        response = self.client.get(
            f"/api/oeufs/statistiques/?lot={self.lot.id}&date_debut={today}&date_fin={today}"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["nombre_poules_vivantes"], 100)
        self.assertEqual(response.data["oeufs_collectes"], 80)
        self.assertEqual(response.data["oeufs_commercialisables"], 70)
        self.assertEqual(response.data["taux_ponte"], 80.0)
        self.assertEqual(response.data["stock_oeufs"], 70)
        self.assertEqual(response.data["equivalent_plateaux"], 2.33)

    def test_purchase_can_create_a_laying_batch(self):
        response = self.client.post(
            "/api/achats/create/",
            {
                "nom_lot": "Nouvelles pondeuses",
                "espece": self.espece.id,
                "quantite": 50,
                "prix_total": "50000.00",
                "prix_unitaire": "1000.00",
                "date": "2026-09-21",
                "type_production": "OEUFS",
                "statut_production": "PONTE",
                "date_debut_ponte": "2026-09-01",
            },
            format="json",
        )

        self.assertEqual(response.status_code, 201)
        lot = Lot.objects.get(id=response.data["lot_id"])
        self.assertEqual(lot.type_production, "OEUFS")
        self.assertEqual(lot.statut_production, "PONTE")
        self.assertEqual(lot.date_debut_ponte, date(2026, 9, 1))
