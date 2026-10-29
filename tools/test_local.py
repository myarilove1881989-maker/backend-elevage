"""Synthetic tests only; no production DSN or SMTP accepted."""
import argparse, json, os, pathlib, secrets, socket, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--postgres', action='store_true')
parser.add_argument('--ci-postgres', action='store_true')
parser.add_argument('--pg-bin', default='C:/Program Files/PostgreSQL/18/bin')
parser.add_argument('labels', nargs='*')
args = parser.parse_args()
sys.path.insert(0, str(ROOT))
os.environ.update(SECRET_KEY='synthetic-local-test-only-elevage-20261029', DEBUG='true', APP_ENV='test', RENDER='false', DATABASE_URL='', DJANGO_SETTINGS_MODULE='config.settings', EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', OFFLINE_SIGNING_PRIVATE_KEY='', SECURE_SSL_REDIRECT='false')
cluster = None
db = {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}

def run(command, label):
    with (cluster / (label + '.log')).open('wb') as output:
        result = subprocess.run(list(map(str, command)), stdout=output, stderr=output, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise RuntimeError(label + ' failed; inspect local private diagnostics')

if args.postgres:
    cluster = ROOT / '.local' / 'postgres-tests'
    cluster.mkdir(parents=True, exist_ok=True)
    pg = pathlib.Path(args.pg_bin)
    if not (pg / 'pg_ctl.exe').is_file():
        raise SystemExit('PostgreSQL binaries unavailable; supply --pg-bin')
    config = cluster / 'local-config.json'
    if not config.exists():
        cfg = {'password': secrets.token_urlsafe(36), 'port': 57831, 'user': 'elevage_test'}
        config.write_text(json.dumps(cfg))
        (cluster / 'password').write_text(cfg['password'])
        run([pg / 'initdb.exe', '-D', cluster / 'data', '-U', cfg['user'], '--pwfile', cluster / 'password', '--auth=scram-sha-256', '--encoding=UTF8', '--locale=C'], 'initdb')
        with (cluster / 'data/postgresql.conf').open('a') as stream:
            stream.write("\nlisten_addresses='127.0.0.1'\nport=57831\nshared_buffers='32MB'\nmax_connections=30\nlog_statement='none'\nlog_min_error_statement='panic'\n")
    cfg = json.loads(config.read_text())
    assert cfg['port'] == 57831 and cfg['user'] == 'elevage_test'
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', 57831)) == 0:
            raise SystemExit('Test port already in use; no existing server is modified')
    run([pg / 'pg_ctl.exe', '-D', cluster / 'data', '-l', cluster / 'server.log', '-w', 'start'], 'start')
    db = {'ENGINE': 'django.db.backends.postgresql', 'HOST': '127.0.0.1', 'PORT': 57831, 'NAME': 'postgres', 'USER': cfg['user'], 'PASSWORD': cfg['password'], 'TEST': {'NAME': 'test_elevage_dev'}}

if args.ci_postgres:
    if args.postgres or os.environ.get('GITHUB_ACTIONS') != 'true':
        raise SystemExit('CI PostgreSQL mode requires GitHub Actions')
    db = {'ENGINE': 'django.db.backends.postgresql', 'HOST': '127.0.0.1', 'PORT': 5432, 'NAME': 'postgres', 'USER': 'elevage_test', 'PASSWORD': 'synthetic-ci-only-password', 'TEST': {'NAME': 'test_elevage_dev'}}

original_connect = socket.socket.connect
def local_only(sock, address):
    if not isinstance(address, tuple) or address[0] not in {'127.0.0.1', '::1'}:
        raise RuntimeError('External network forbidden in local tests')
    return original_connect(sock, address)
socket.socket.connect = local_only
try:
    import django
    django.setup()
    from django.conf import settings
    settings.DATABASES = {'default': db, 'health': dict(db)}
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
    settings.EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
    from django.core.management import call_command
    call_command('makemigrations', check=True, dry_run=True, verbosity=1)
    from django.test.utils import get_runner
    labels = args.labels or ['core.tests.test_password_reset', 'core.tests.test_permission_foundations', 'core.tests.test_android107_offline']
    failures = get_runner(settings)(verbosity=1, interactive=False).run_tests(labels)
    raise SystemExit(bool(failures))
finally:
    if cluster:
        run([pg / 'pg_ctl.exe', '-D', cluster / 'data', '-m', 'fast', '-w', 'stop'], 'stop')
