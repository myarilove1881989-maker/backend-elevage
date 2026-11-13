"""D: synthetic PostgreSQL qualification; no external host, no deployment."""
import os,sys
from pathlib import Path
root=Path(__file__).resolve().parents[1]
assert root.drive.upper()=='D:'
temp=root.parent/'qualification/tmp'
temp.mkdir(parents=True,exist_ok=True)
os.environ.update(TEMP=str(temp),TMP=str(temp),TMPDIR=str(temp),PYTHONDONTWRITEBYTECODE='1',PYTHONUNBUFFERED='1')
runner=root/'tools/test_local.py'
sys.argv=[str(runner),'--postgres',*(sys.argv[1:] or ['core.tests','pisciculture.tests'])]
source=runner.read_text(encoding='utf-8').replace("'postgres-tests'","'phase3e-postgres'").replace('57831','57867').replace('verbosity=1, interactive=False','verbosity=2, interactive=False')
exec(compile(source,str(runner),'exec'),{'__file__':str(runner),'__name__':'__main__'})
