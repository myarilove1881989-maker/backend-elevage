import io
import json
import tempfile
import uuid
from datetime import timedelta
from pathlib import Path
from unittest import skipUnless

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.test import TestCase
from django.utils import timezone

from core.models import User, DeviceRegistration, DeviceChallenge, OfflineAuthorization


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

    def uuid_fixture(self):
        device = DeviceRegistration.objects.create(exploitation=self.user.exploitation,
            installation_uuid=uuid.uuid4(), display_name='UUID snapshot fixture',
            public_key='snapshot-only fixture', status='ACTIVE', is_primary_writer=True)
        challenge = DeviceChallenge.objects.create(id=uuid.UUID('ffffffff-ffff-ffff-ffff-ffffffffffff'),
            device=device, user=self.user, purpose='WRITE', expires_at=timezone.now()+timedelta(minutes=5))
        grant = OfflineAuthorization.objects.create(id=uuid.UUID('ffffffff-ffff-ffff-ffff-ffffffffffff'),
            membership=self.user.memberships.get(exploitation=self.user.exploitation), device=device,
            capabilities={}, rights_version=1, write_generation=1,
            expires_at=timezone.now()+timedelta(days=7))
        return device, challenge, grant

    def test_uuid_baseline_excludes_later_smaller_identifiers(self):
        device, challenge, grant = self.uuid_fixture()
        self.audit('write')
        DeviceChallenge.objects.create(id=uuid.UUID('00000000-0000-0000-0000-000000000001'),
            device=device, user=self.user, purpose='WRITE', expires_at=challenge.expires_at)
        OfflineAuthorization.objects.create(id=uuid.UUID('00000000-0000-0000-0000-000000000001'),
            membership=grant.membership, device=device, capabilities={}, rights_version=1,
            write_generation=1, expires_at=grant.expires_at)
        result = self.audit('compare')
        self.assertTrue(result['historical_data_unchanged'])
        for table in ('core_devicechallenge', 'core_offlineauthorization'):
            self.assertEqual(result['counts'][table], 1)
            self.assertEqual(result['current_counts'][table], 2)

    def test_changed_historical_uuid_record_is_detected(self):
        _, challenge, _ = self.uuid_fixture()
        self.audit('write')
        challenge.consumed_at = timezone.now()
        challenge.save(update_fields=['consumed_at'])
        with self.assertRaisesRegex(CommandError, 'core_devicechallenge'):
            self.audit('compare')

    def test_deleted_historical_uuid_record_is_detected(self):
        _, challenge, _ = self.uuid_fixture()
        self.audit('write')
        challenge.delete()
        with self.assertRaisesRegex(CommandError, 'core_devicechallenge'):
            self.audit('compare')

    def test_empty_uuid_baseline_stays_empty_after_new_records(self):
        self.audit('write')
        self.uuid_fixture()
        result = self.audit('compare')
        self.assertTrue(result['historical_data_unchanged'])
        for table in ('core_devicechallenge', 'core_offlineauthorization'):
            self.assertEqual(result['counts'][table], 0)
            self.assertEqual(result['current_counts'][table], 1)

    def test_ambiguous_legacy_uuid_watermark_requires_explicit_new_baseline(self):
        self.audit('write')
        baseline = json.loads(self.snapshot.read_text())
        snapshot = baseline['tables']['core_devicechallenge']
        snapshot.pop('historical_ids')
        snapshot['through_id'] = 0
        self.snapshot.write_text(json.dumps(baseline))
        with self.assertRaisesRegex(CommandError, 'UUID baseline'):
            self.audit('compare')

    @skipUnless(connection.vendor == "sqlite", "SQLite-only guard")
    def test_sqlite_is_refused_without_explicit_local_validation(self):
        with self.assertRaisesRegex(CommandError, "requires PostgreSQL"):
            call_command("production_audit", write=self.snapshot, stdout=io.StringIO())
