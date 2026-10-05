import io
import json
import tempfile
from pathlib import Path
from unittest import skipUnless

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase

from core.models import User


class ProductionAuditTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="audit-fixture", password="synthetic-local-password")
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.snapshot = Path(self.directory.name) / "snapshot.json"

    def audit(self, mode):
        output = io.StringIO()
        call_command("production_audit", **{mode: self.snapshot}, allow_sqlite=True, stdout=output)
        return json.loads(output.getvalue().split(" ", 1)[1])

    def test_equal_data_survives_comparison_without_modification(self):
        self.audit("write")
        result = self.audit("compare")
        self.assertTrue(result["historical_data_unchanged"])
        self.assertEqual(result["counts"]["core_user"], 1)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "audit-fixture")

    def test_changed_historical_record_is_detected(self):
        self.audit("write")
        self.user.username = "changed-fixture"
        self.user.save(update_fields=["username"])
        with self.assertRaisesRegex(CommandError, "core_user"):
            self.audit("compare")

    def test_deleted_historical_record_is_detected(self):
        self.audit("write")
        self.user.delete()
        with self.assertRaises(CommandError):
            self.audit("compare")

    def test_new_records_do_not_hide_or_invalidate_historical_comparison(self):
        self.audit("write")
        User.objects.create_user(username="later-fixture", password="another-synthetic-password")
        result = self.audit("compare")
        self.assertTrue(result["historical_data_unchanged"])
        self.assertEqual(result["counts"]["core_user"], 1)
        self.assertEqual(result["current_counts"]["core_user"], 2)

    @skipUnless(connection.vendor == "sqlite", "SQLite-only guard")
    def test_sqlite_is_refused_without_explicit_local_validation(self):
        with self.assertRaisesRegex(CommandError, "requires PostgreSQL"):
            call_command("production_audit", write=self.snapshot, stdout=io.StringIO())
