import json, pathlib, subprocess, time
ROOT=pathlib.Path("/home/ubuntu/data/dockercompose/ghelper/releases/orchestration-20261117")
VOL="ghelper-orchestration-restore-drill"
CONTAINER="ghelper-orchestration-restore-drill"
OLD="ghelper:rollback-orchestration-20261117"
def run(*args, capture=False):
 p=subprocess.run(["docker",*args],check=True,text=True,stdout=subprocess.PIPE if capture else None)
 return p.stdout if capture else None
run("volume","create",VOL)
try:
 run("run","--rm","--network","none","--user","0","-v",VOL+":/data","-v",str(ROOT/"backup")+":/backup:ro",
     "--entrypoint","python",OLD,"-c","import os,pathlib,tarfile; p=pathlib.Path('/data'); t=tarfile.open('/backup/data.tar.gz'); t.extractall('/',filter='data'); t.close(); [os.chown(x,10001,10001) for x in [p,*p.rglob('*')]]")
 run("run","-d","--name",CONTAINER,"--network","none","--env-file",str(ROOT/"runtime.env"),"-v",VOL+":/data",OLD)
 for _ in range(30):
  p=subprocess.run(["docker","exec",CONTAINER,"curl","-fsS","http://127.0.0.1:8000/readyz"],capture_output=True)
  if p.returncode==0: break
  time.sleep(1)
 else: raise RuntimeError("restored application failed readiness")
 run("exec",CONTAINER,"python","-c","import sqlite3; c=sqlite3.connect('/data/ghelper.db'); assert c.execute('pragma integrity_check').fetchone()[0]=='ok'; assert c.execute('select version_num from alembic_version').fetchone()[0]=='0011'; print('Full-volume restore on old image as UID 10001: readiness and integrity PASS')")
 print("RESTORE_DRILL_COMPLETE",flush=True)
finally:
 subprocess.run(["docker","rm","-f",CONTAINER],check=False)
 run("volume","rm",VOL)
