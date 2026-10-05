from unittest import skipUnless

from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase

from core import egg_services
from core.management.commands.production_release_check import cleanup_test_user, create_fixture, database_checks, sale_data
from core.models import User, VenteOeufs


class ProductionReleaseCheckTests(TestCase):
    def test_cleanup_removes_protected_test_relations_and_preserves_another_tenant(self):
        regular = User.objects.create_user(username="regular-fixture")
        name = "__release_qa_0123456789abcdef_db"
        user = User.objects.create_user(username=name, email=name + "@example.invalid")
        lot, customer = create_fixture(user, "Cleanup fixture")
        egg_services.create_egg_sale(validated_data=sale_data(lot, customer), user=user)
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

    @skipUnless(connection.vendor == "sqlite", "SQLite-only guard")
    def test_concurrency_test_requires_postgresql_before_creating_any_fixture(self):
        before = User.objects.count()
        with self.assertRaisesRegex(CommandError, "require PostgreSQL"):
            database_checks()
        self.assertEqual(User.objects.count(), before)
