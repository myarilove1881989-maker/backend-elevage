"""Guarded, single-session migration of the new pilot database only."""
import json
import os
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from core.pilot_checks import verify_database


class Command(BaseCommand):
    help = 'Verify pilot identity, migrate under a session lock, create fictitious accounts, run checks.'

    def handle(self, *args, **options):
        identity = verify_database()
        self.stdout.write('PILOT_DATABASE_BEFORE ' + json.dumps(identity, sort_keys=True))
        if identity['tables'] and 'django_migrations' not in identity['tables']:
            raise CommandError('PILOT_IDENTITY: nonempty unrecognized schema')
        with connection.cursor() as cursor:
            cursor.execute('SELECT pg_try_advisory_lock(18819891010)')
            if not cursor.fetchone()[0]:
                raise CommandError('PILOT_DEPLOY: another migration is running')
        try:
            if identity['tables']:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT to_regclass('public.core_user')")
                    exists = cursor.fetchone()[0]
                    if exists:
                        cursor.execute("SELECT username FROM core_user WHERE username NOT IN ('pilot-owner', 'pilot-operator')")
                        if cursor.fetchone():
                            raise CommandError('PILOT_DEPLOY: unexpected accounts; manual review required')
            call_command('migrate', interactive=False)
            from core.models import User, Espece, Lot, Client
            with transaction.atomic():
                owner = User.objects.filter(username='pilot-owner').first()
                if owner is None:
                    owner = User.objects.create_user(username='pilot-owner',
                        password=os.environ['PILOT_OWNER_PASSWORD'], email='pilot-owner@example.invalid')
                    farm = owner.exploitation
                    farm.nom = 'PILOTE — Ferme fictive'
                    farm.save(update_fields=['nom'])
                    species = Espece.objects.create(nom='PILOTE Poules', exploitation=farm)
                    Lot.objects.create(nom='PILOTE Lot fictif', exploitation=farm, espece=species,
                        date_debut='2026-10-10', type_production='OEUFS', statut_production='PONTE')
                    Client.objects.create(nom='PILOTE Client fictif', exploitation=farm)
                if owner.email != 'pilot-owner@example.invalid' or owner.is_staff or owner.is_superuser:
                    raise CommandError('PILOT_FIXTURES: unexpected owner')
                operator = User.objects.filter(username='pilot-operator').first()
                if operator is None:
                    operator = User.objects.create_user(username='pilot-operator', exploitation=owner.exploitation,
                        password=os.environ['PILOT_OPERATOR_PASSWORD'], email='pilot-operator@example.invalid')
                if operator.exploitation_id != owner.exploitation_id or operator.email != 'pilot-operator@example.invalid':
                    raise CommandError('PILOT_FIXTURES: unexpected operator')
            self.stdout.write('PILOT_FIXTURES owner=pilot-owner operator=pilot-operator offline_policy=false_initially')
            call_command('pilot_smoke')
            call_command('showmigrations', plan=True)
            self.stdout.write('PILOT_DEPLOY_COMPLETE')
        finally:
            with connection.cursor() as cursor:
                cursor.execute('SELECT pg_advisory_unlock(18819891010)')
