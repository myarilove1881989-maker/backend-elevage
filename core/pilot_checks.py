"""Non-sensitive identity proof required before any pilot database write."""
import os
from django.conf import settings
from django.core.management.base import CommandError
from django.db import connection
from config.pilot_policy import validate_environment


def verify_database():
    validate_environment(os.environ)
    if settings.SETTINGS_MODULE != 'config.pilot_settings' or settings.APP_ENV != 'pilot':
        raise CommandError('PILOT_IDENTITY: pilot settings required')
    db = connection.settings_dict
    if (connection.vendor != 'postgresql' or db['HOST'] != os.environ['PILOT_DB_HOST']
            or db['NAME'] != 'elevage_pilot' or db['USER'] != 'elevage_pilot_owner'):
        raise CommandError('PILOT_IDENTITY: configured database mismatch')
    with connection.cursor() as cursor:
        cursor.execute('SELECT current_database(), current_user, current_setting(\'server_version\')')
        name, user, version = cursor.fetchone()
        if (name, user) != ('elevage_pilot', 'elevage_pilot_owner') or not version.startswith('18.'):
            raise CommandError('PILOT_IDENTITY: connected database mismatch')
        cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
        tables = [row[0] for row in cursor.fetchall()]
    return {'database': name, 'role': user, 'host': db['HOST'], 'version': version, 'tables': tables}
