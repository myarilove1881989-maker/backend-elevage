"""Windows-only synthetic PostgreSQL rehearsal. Never reads DATABASE_URL."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
BIN = Path('C:/Program Files/PostgreSQL/18/bin')
DATA = ROOT / ('.phase2j-postgres-' + uuid.uuid4().hex)
PROOF = ROOT / 'phase2j-evidence' / DATA.name
PORT = '55440'
ENV = {**os.environ, 'SECRET_KEY': 'phase2j-synthetic-only-secret-not-for-deployment',
       'DEBUG': 'true', 'DATABASE_URL': '', 'PGHOST': '127.0.0.1',
       'PGPORT': PORT, 'PGUSER': 'phase2j_test', 'PGDATABASE': 'postgres', 'PGCONNECT_TIMEOUT': '5'}
for name in ('PGPASSWORD', 'PGSERVICE', 'PGSERVICEFILE', 'PGOPTIONS'):
    ENV.pop(name, None)

def run(args, name):
    log = PROOF / (name + '.log')
    with log.open('w', encoding='utf-8') as output:
        result = subprocess.run([str(a) for a in args], cwd=ROOT, env=ENV,
            stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT, timeout=600,
            creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(f'{name} failed; inspect its evidence log')
    return log.read_text(encoding='utf-8')

def sql(db, query, name):
    return run([BIN/'psql.exe', '-X', '-v', 'ON_ERROR_STOP=1', '-At', '-d', db, '-c', query], name).strip()

def main():
    assert not DATA.exists(), 'Use a fresh rehearsal directory; never reuse an existing cluster'
    PROOF.mkdir(parents=True, exist_ok=True)
    run([BIN/'initdb.exe', '-D', DATA, '-U', 'phase2j_test', '--auth=trust', '--encoding=UTF8', '--locale=C'], 'initdb')
    # Trust is restricted to this disposable localhost cluster; no production credentials.
    started = False
    try:
        run([BIN/'pg_ctl.exe', '-D', DATA, '-l', PROOF/'postgres.log', '-o',
             f'-h 127.0.0.1 -p {PORT}', '-w', 'start'], 'start')
        started = True
        sql('postgres', 'CREATE DATABASE phase2j_source_test', 'create-source')
        sql('postgres', 'CREATE DATABASE phase2j_restore_test', 'create-restore')
        ENV['DATABASE_URL'] = f'postgresql://phase2j_test@127.0.0.1:{PORT}/phase2j_source_test'
        run([sys.executable, 'manage.py', 'migrate', '--noinput'], 'migrate')
        run([sys.executable, 'manage.py', 'makemigrations', '--check', '--dry-run'], 'migration-drift')
        run([sys.executable, 'manage.py', 'test', '--noinput'], 'postgres-tests')
        sql('phase2j_source_test', "CREATE TABLE phase2j_synthetic_probe (id integer primary key, declaration_uuid uuid unique, amount numeric(12,2)); INSERT INTO phase2j_synthetic_probe VALUES (1,'11111111-2222-4333-8444-555555555555',123.45)", 'synthetic-probe')
        dump = PROOF/'synthetic.dump'
        run([BIN/'pg_dump.exe', '-Fc', '--no-owner', '--no-acl', '-d', 'phase2j_source_test', '-f', dump], 'dump')
        run([BIN/'pg_restore.exe', '--exit-on-error', '--single-transaction', '--no-owner', '--no-acl', '-d', 'phase2j_restore_test', dump], 'restore')
        queries = {
            'migrations': "SELECT app||':'||name FROM django_migrations ORDER BY app,name",
            'constraints': "SELECT conname||':'||pg_get_constraintdef(oid) FROM pg_constraint WHERE connamespace='public'::regnamespace ORDER BY conname,pg_get_constraintdef(oid)",
            'triggers': "SELECT tgname||':'||pg_get_triggerdef(oid) FROM pg_trigger WHERE NOT tgisinternal ORDER BY tgname",
            'probe': 'SELECT id,declaration_uuid,amount FROM phase2j_synthetic_probe ORDER BY id',
            'policy': 'SELECT count(*) FROM core_exploitation WHERE offline_policy_enabled',
        }
        checks = {}
        for name, query in queries.items():
            before = sql('phase2j_source_test', query, 'source-'+name)
            after = sql('phase2j_restore_test', query, 'restored-'+name)
            assert before == after, f'Restore mismatch: {name}'
            checks[name] = True
        for action in ('UPDATE', 'DELETE'):
            statement = ('UPDATE core_auditevent SET action=action' if action == 'UPDATE' else 'DELETE FROM core_auditevent')
            result = subprocess.run([str(BIN/'psql.exe'), '-X', '-v', 'ON_ERROR_STOP=1', '-d', 'phase2j_restore_test', '-c', statement], env=ENV, capture_output=True, text=True, creationflags=subprocess.CREATE_NO_WINDOW)
            assert result.returncode != 0 and 'append-only' in result.stderr
            checks['audit_rejects_'+action.lower()] = True
        (PROOF/'restore-result.json').write_text(json.dumps({'production_data': False,
            'host': '127.0.0.1', 'port': int(PORT), 'postgres_version': sql('postgres','SHOW server_version','version'),
            'dump_sha256': hashlib.sha256(dump.read_bytes()).hexdigest(), 'checks': checks}, indent=2)+'\n')
        print('PHASE2J_SYNTHETIC_RESTORE_PASSED')
    finally:
        if started:
            run([BIN/'pg_ctl.exe', '-D', DATA, '-m', 'fast', '-w', 'stop'], 'stop')

if __name__ == '__main__':
    main()
