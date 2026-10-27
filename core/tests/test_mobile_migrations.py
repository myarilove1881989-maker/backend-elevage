from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class MembershipMigrationTests(TransactionTestCase):
    latest = [('core', '0018_audit_append_only_postgresql')]

    def setUp(self):
        executor = MigrationExecutor(connection)
        executor.migrate([('core', '0015_mouvement_lot_origine')])
        self.apps = executor.loader.project_state([('core', '0015_mouvement_lot_origine')]).apps

    def tearDown(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes('core'))
        super().tearDown()

    def migrate(self):
        executor = MigrationExecutor(connection)
        executor.migrate(self.latest)
        return executor.loader.project_state(self.latest).apps

    def test_bootstrap_preserves_historical_authors_and_farm(self):
        User = self.apps.get_model('core', 'User')
        Farm = self.apps.get_model('core', 'Exploitation')
        Task = self.apps.get_model('core', 'Task')
        owner = User.objects.create(username='historical-owner')
        farm = Farm.objects.create(nom='Historique', proprietaire=owner)
        User.objects.filter(pk=owner.pk).update(exploitation=farm)
        operator = User.objects.create(username='historical-operator', exploitation=farm)
        task = Task.objects.create(exploitation=farm, title='Historique', date='2026-01-01')
        apps = self.migrate()
        Member = apps.get_model('core', 'ExploitationMembership')
        self.assertEqual(Member.objects.get(user_id=owner.pk, exploitation_id=farm.pk).role, 'OWNER')
        self.assertEqual(Member.objects.get(user_id=operator.pk, exploitation_id=farm.pk).role, 'OPERATEUR')
        migrated = apps.get_model('core', 'Task').objects.get(pk=task.pk)
        self.assertIsNone(migrated.created_by_id)
        self.assertEqual(migrated.status, 'TODO')
        self.assertEqual(apps.get_model('core', 'AuditEvent').objects.count(), 0)
        self.assertFalse(apps.get_model('core', 'Exploitation').objects.get(pk=farm.pk).offline_policy_enabled)

    def test_owner_inconsistency_is_preserved_for_explicit_review(self):
        User = self.apps.get_model('core', 'User')
        Farm = self.apps.get_model('core', 'Exploitation')
        owner = User.objects.create(username='ambiguous-owner')
        other = User.objects.create(username='other-owner')
        first = Farm.objects.create(nom='First', proprietaire=owner)
        second = Farm.objects.create(nom='Second', proprietaire=other)
        User.objects.filter(pk=owner.pk).update(exploitation=second)
        apps = self.migrate()
        self.assertEqual(apps.get_model('core', 'User').objects.get(pk=owner.pk).exploitation_id, second.pk)
        member = apps.get_model('core', 'ExploitationMembership')
        self.assertEqual(member.objects.get(user_id=owner.pk, exploitation_id=first.pk).role, 'OWNER')
        self.assertEqual(member.objects.get(user_id=owner.pk, exploitation_id=second.pk).role, 'OPERATEUR')
