from decimal import Decimal
from unittest import skipUnless

from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase

from core.management.commands.production_release_check import cleanup_test_user, create_fixture, database_checks
from django.db.models.deletion import ProtectedError
from core.models import (AffectationMouvementOeufs, CollecteOeufs, MouvementOeufs,
                         User, Vente, VenteOeufs)


class ProductionReleaseCheckTests(TestCase):
    def test_cleanup_removes_protected_test_relations_and_preserves_another_tenant(self):
        regular = User.objects.create_user(username="regular-fixture")
        name = "__release_qa_0123456789abcdef_db"
        user = User.objects.create_user(username=name, email=name + "@example.invalid")
        lot, customer = create_fixture(user, "Cleanup fixture")
        # Cleanup tests historical relations, not authorization to create sales.
        # The current owner cannot perform business writes through the service.
        # Keep this single-owner synthetic tenant and construct its old rows.
        sale = Vente.objects.create(lot=lot, client=customer, created_by=user,
            quantite=80, prix_unitaire=Decimal("1.00"))
        eggs = VenteOeufs.objects.create(vente=sale, exploitation=user.exploitation,
            lot=lot, created_by=user, conditionnement="UNITE",
            nombre_conditionnements=80, oeufs_par_conditionnement=1, nombre_oeufs=80,
            prix_unitaire_conditionnement=Decimal("1.00"), montant_total=Decimal("80.00"))
        movement = MouvementOeufs.objects.create(exploitation=user.exploitation,
            lot=lot, type_mouvement="VENTE", quantite=80, vente_oeufs=eggs, created_by=user)
        collection = CollecteOeufs.objects.get(lot=lot)
        AffectationMouvementOeufs.objects.create(
            mouvement=movement, collecte=collection, quantite=80)
        with self.assertRaises(ProtectedError):
            collection.delete()
        self.assertTrue(VenteOeufs.objects.filter(exploitation=user.exploitation).exists())
        self.assertGreater(cleanup_test_user(name), 0)
        self.assertFalse(User.objects.filter(username=name).exists())
        self.assertTrue(User.objects.filter(pk=regular.pk).exists())
        self.assertTrue(regular.exploitation.users.filter(pk=regular.pk).exists())

    def test_cleanup_refuses_a_regular_user(self):
        regular = User.objects.create_user(username="regular-fixture")
        with self.assertRaises(CommandError):
            cleanup_test_user(regular.username)
        self.assertTrue(User.objects.filter(pk=regular.pk).exists())

    def test_cleanup_refuses_a_shared_synthetic_tenant(self):
        name = "__release_qa_0123456789abcdef_db"
        owner = User.objects.create_user(username=name, email=name + "@example.invalid")
        operator = User.objects.create_user(username="synthetic-shared-operator",
            exploitation=owner.exploitation)
        lot, customer = create_fixture(owner, "Shared cleanup guard")
        with self.assertRaisesRegex(CommandError, "shared or unowned"):
            cleanup_test_user(name)
        self.assertTrue(User.objects.filter(pk=owner.pk).exists())
        self.assertTrue(User.objects.filter(pk=operator.pk).exists())
        self.assertTrue(CollecteOeufs.objects.filter(lot=lot).exists())

    @skipUnless(connection.vendor == "sqlite", "SQLite-only guard")
    def test_concurrency_test_requires_postgresql_before_creating_any_fixture(self):
        before = User.objects.count()
        with self.assertRaisesRegex(CommandError, "require PostgreSQL"):
            database_checks()
        self.assertEqual(User.objects.count(), before)
