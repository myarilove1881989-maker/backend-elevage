"""Rehearse a full logical restore using fresh local synthetic databases only."""
import hashlib, json, os, secrets, socket, subprocess, sys, uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
CLUSTER=ROOT/'.local/postgres-tests'
WORK=ROOT/'.local'/('restore-'+uuid.uuid4().hex[:10])
WORK.mkdir(parents=True,exist_ok=False)
PG=Path('C:/Program Files/PostgreSQL/18/bin')
cfg=json.loads((CLUSTER/'local-config.json').read_text(encoding='utf-8'))
assert cfg['port']==57831 and cfg['user']=='elevage_test'
with socket.socket() as probe:
    assert probe.connect_ex(('127.0.0.1',57831))!=0,'Local test port is already in use'

def run(args,name,env=None):
    with (WORK/(name+'.log')).open('wb') as output:
        result=subprocess.run(list(map(str,args)),env=env,stdout=output,stderr=output,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise RuntimeError(name+' failed; local diagnostics retained')

run([PG/'pg_ctl.exe','-D',CLUSTER/'data','-l',WORK/'server.log','-w','start'],'start')
try:
    import psycopg2
    from psycopg2 import sql
    connect=lambda name:psycopg2.connect(host='127.0.0.1',port=57831,user=cfg['user'],password=cfg['password'],dbname=name)
    source='qa_backup_'+uuid.uuid4().hex[:12];target='qa_restore_'+uuid.uuid4().hex[:12]
    admin=connect('postgres');admin.autocommit=True
    with admin.cursor() as cursor:
        for name in [source,target]:cursor.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    admin.close()
    sys.path.insert(0,str(ROOT))
    os.environ.update(SECRET_KEY='synthetic-restore-test-only-20261029',DEBUG='true',APP_ENV='test',RENDER='false',DATABASE_URL='',DJANGO_SETTINGS_MODULE='config.settings',EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',OFFLINE_SIGNING_PRIVATE_KEY='')
    from django.conf import settings
    settings.DATABASES={'default':{'ENGINE':'django.db.backends.postgresql','HOST':'127.0.0.1','PORT':57831,'NAME':source,'USER':cfg['user'],'PASSWORD':cfg['password']}}
    settings.PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher']
    import django
    django.setup()
    from django.core.management import call_command
    call_command('migrate',interactive=False,verbosity=0)
    from core.models import User,PasswordResetLimit
    from django.utils import timezone
    from datetime import timedelta
    User.objects.create_user(username='synthetic-backup-owner',email='backup@example.test',password='SyntheticOnly!113')
    PasswordResetLimit.objects.create(key='synthetic-backup-limit',count=2,expires_at=timezone.now()+timedelta(hours=1))
    env=dict(os.environ)
    for key in list(env):
        if key.startswith('PG'):env.pop(key)
    env.update(PGHOST='127.0.0.1',PGPORT='57831',PGUSER=cfg['user'],PGPASSWORD=cfg['password'],PGSSLMODE='disable')
    backup=WORK/'synthetic.dump'
    run([PG/'pg_dump.exe','--format=custom','--no-owner','--no-privileges','--file',backup,'--dbname',source],'dump',env)
    run([PG/'pg_restore.exe','--list',backup],'list',env)
    run([PG/'pg_restore.exe','--exit-on-error','--no-owner','--no-privileges','--dbname',target,backup],'restore',env)

    def snapshot(name):
        connection=connect(name);result={}
        with connection.cursor() as cursor:
            cursor.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")
            tables=[row[0] for row in cursor.fetchall()]
            for table in tables:
                cursor.execute(sql.SQL('SELECT * FROM {} ORDER BY 1').format(sql.Identifier(table)))
                rows=cursor.fetchall()
                digest=hashlib.sha256(json.dumps(rows,default=str,ensure_ascii=False).encode()).hexdigest()
                result[table]={'rows':len(rows),'sha256':digest}
        connection.close();return result
    before=snapshot(source);after=snapshot(target)
    assert before==after,'Restored data differs'
    assert 'core_passwordresetemail' in after and 'core_passwordresetlimit' in after
    report={'synthetic_only':True,'postgres_port':57831,'dump_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),'archive_list_verified':True,'restore_in_new_empty_database':True,'tables_compared':len(after),'all_row_hashes_identical':True,'schema_0038_present':True}
    (ROOT.parent/'evidence/web113-restore-proof.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report),flush=True)
finally:
    try:
        from django.db import connections
        connections.close_all()
    finally:
        run([PG/'pg_ctl.exe','-D',CLUSTER/'data','-m','fast','-w','stop'],'stop')
