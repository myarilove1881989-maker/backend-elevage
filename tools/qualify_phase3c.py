"""Local synthetic PostgreSQL only; private files reside inside this D: checkout."""
import os,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
if root.drive.upper()!='D:':raise SystemExit('D_DRIVE_REQUIRED')
temp=root.parent/'phase3c'/'tmp';temp.mkdir(exist_ok=True)
os.environ.update(TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp),PYTHONDONTWRITEBYTECODE='1')
runner=root/'tools/test_local.py'
sys.argv=[str(runner),'--postgres',*sys.argv[1:]]
source=runner.read_text(encoding='utf-8').replace("'postgres-tests'","'phase3c-postgres'").replace('57831','57859')
exec(compile(source,str(runner),'exec'),{'__file__':str(runner),'__name__':'__main__'})
