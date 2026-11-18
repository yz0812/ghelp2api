import json, os, pathlib, shutil, subprocess, sys, time
ROOT = pathlib.Path("/home/ubuntu/data/dockercompose/ghelper")
REL = ROOT / "releases/orchestration-20261117"
IMAGE = "ghelper:orchestration-20261117-v130-r3"
OLD = "ghelper:chain-selection-20261117"
def run(args, capture=False):
    p = subprocess.run(args, cwd=ROOT, check=True, text=True, stdout=subprocess.PIPE if capture else None)
    return p.stdout.strip() if capture else None
def docker(*args, capture=False): return run(["docker", *args], capture)
def envfile():
    data = json.loads(docker("inspect", "ghelper", capture=True))[0]
    path = REL / "runtime.env"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        for value in data["Config"]["Env"]:
            if not value.startswith(("SCHEDULER_ENABLED=", "MIHOMO_BIN=")): f.write(value + "\n")
        f.write("SCHEDULER_ENABLED=false\nMIHOMO_BIN=off\n")
    return data
def helper(image, data, code):
    return docker("run", "--rm", "--network", "none", "--user", "0", "--env-file", str(REL/"runtime.env"),
                  "-v", str(data)+":/data", "-v", str(REL)+":/release", "--entrypoint", "python", image, "-c", code)
def ready():
    for _ in range(30):
        p = subprocess.run(["docker", "exec", "ghelper", "curl", "-fsS", "http://127.0.0.1:8000/readyz"], capture_output=True)
        if p.returncode == 0: return
        time.sleep(2)
    raise RuntimeError("readiness timeout")
stage = sys.argv[1]
if stage == "rehearse":
    envfile()
    (REL/"preupgrade").mkdir(exist_ok=True)
    docker("exec", "ghelper", "python", "-c", "import sqlite3; a=sqlite3.connect('/data/ghelper.db'); b=sqlite3.connect('/tmp/orchestration-rehearsal.db'); a.backup(b); b.close(); a.close()")
    subprocess.run(["docker", "exec", "ghelper", "cat", "/tmp/orchestration-rehearsal.db"], stdout=open(REL/"preupgrade/ghelper.db", "wb"), check=True)
    for name in ("migrated", "restored"):
        (REL/name).mkdir(exist_ok=True)
        shutil.copy2(REL/"preupgrade/ghelper.db", REL/name/"ghelper.db")
    helper(IMAGE, REL/"migrated", """
import sqlite3, os
from pathlib import Path
from app.db import make_db
from app.security import Secrets
p='/data/ghelper.db'
c=sqlite3.connect(p)
tables=['chains','subscriptions','entries','exits','accounts','sources']
cols={t:[r[1] for r in c.execute('pragma table_info('+t+')')] for t in tables}
before={t:c.execute('select '+','.join(cols[t])+' from '+t+' order by id').fetchall() for t in tables}
assert c.execute('select version_num from alembic_version').fetchone()[0]=='0011'
c.close()
engine,_=make_db(Path('/data')); engine.dispose()
c=sqlite3.connect(p)
assert c.execute('pragma integrity_check').fetchone()[0]=='ok'
assert not c.execute('pragma foreign_key_check').fetchall()
assert c.execute('select version_num from alembic_version').fetchone()[0]=='0012'
for t in tables:
 assert before[t]==c.execute('select '+','.join(cols[t])+' from '+t+' order by id').fetchall(), t
for cipher, monitor in c.execute('select password_cipher,monitor_enabled from chains'):
 Secrets(os.environ['ENCRYPTION_KEY']).decrypt(cipher)
 assert monitor==0
print('ARM64 migration: old columns, credentials, subscriptions and integrity PASS; chains='+str(len(before['chains'])))
c.close()
""")
    helper(OLD, REL/"restored", """
import sqlite3
from pathlib import Path
from app.db import make_db
engine,_=make_db(Path('/data')); engine.dispose()
a=sqlite3.connect('/data/ghelper.db'); b=sqlite3.connect('/release/preupgrade/ghelper.db')
assert a.execute('select version_num from alembic_version').fetchone()[0]=='0011'
for t in ['chains','subscriptions','entries','exits','accounts','sources']:
 assert a.execute('select * from '+t+' order by id').fetchall()==b.execute('select * from '+t+' order by id').fetchall(), t
print('ARM64 restore: old image plus old database PASS')
""")
    (REL/"rehearsal.ok").write_text(str(time.time()))
elif stage == "deploy":
    assert (REL/"rehearsal.ok").exists()
    data=envfile()
    assert data["Config"]["Image"]==OLD
    backup=REL/"backup"
    backup.mkdir(mode=0o700)
    for name in ["docker-compose.yml","docker-compose.override.yml",".env"]:
        if (ROOT/name).exists(): shutil.copy2(ROOT/name, backup/name)
    (backup/"metadata.json").write_text(json.dumps({"image":data["Config"]["Image"],"image_id":data["Image"],"time":time.time()}))
    docker("tag", data["Image"], "ghelper:rollback-orchestration-20261117")
    docker("stop", "-t", "30", "ghelper")
    try:
        docker("run","--rm","--network","none","--user","0","-v","ghelper-data:/data:ro","-v",str(backup)+":/backup",
               "--entrypoint","python",OLD,"-c","import tarfile; t=tarfile.open('/backup/data.tar.gz','w:gz'); t.add('/data',arcname='data'); t.close()")
        (ROOT/"docker-compose.override.yml").write_text("services:\n  ghelper:\n    image: "+IMAGE+"\n    pull_policy: never\n    ports:\n      - \"20000-20099:20000-20099/tcp\"\n")
        run(["docker","compose","up","-d","--no-deps","--force-recreate","ghelper"])
        ready()
    except Exception:
        print("Deploy failed; use release.py rollback with complete backup.")
        raise
    (REL/"deployed-at").write_text(str(time.time()))
    print("Deployment ready; observation starts")
elif stage == "status":
    data=json.loads(docker("inspect","ghelper",capture=True))[0]
    print(json.dumps({"image":data["Config"]["Image"],"health":data["State"].get("Health",{}).get("Status"),"restarts":data["RestartCount"],"started":data["State"]["StartedAt"],"elapsed":round(time.time()-float((REL/"deployed-at").read_text())) if (REL/"deployed-at").exists() else None}))
elif stage == "rebuild":
    run(["docker","compose","up","-d","--no-deps","--force-recreate","ghelper"]); ready()
    print("Recreated container and readiness PASS")
elif stage == "rollback":
    backup=REL/"backup"
    assert (backup/"data.tar.gz").is_file()
    docker("stop","-t","30","ghelper")
    docker("run","--rm","--network","none","--user","0","-v","ghelper-data:/data","-v",str(backup)+":/backup:ro",
        "--entrypoint","python",OLD,"-c","import pathlib,shutil,tarfile; p=pathlib.Path('/data'); assert p.resolve()==pathlib.Path('/data'); [(shutil.rmtree(x) if x.is_dir() and not x.is_symlink() else x.unlink()) for x in p.iterdir()]; t=tarfile.open('/backup/data.tar.gz'); t.extractall('/',filter='data'); t.close(); import os; [os.chown(x,10001,10001) for x in [p,*p.rglob('*')]]")
    for name in ["docker-compose.yml","docker-compose.override.yml",".env"]:
        if (backup/name).exists(): shutil.copy2(backup/name,ROOT/name)
    run(["docker","compose","up","-d","--no-deps","--force-recreate","ghelper"]); ready()
    print("Full image + data + Compose rollback PASS")
else: raise ValueError(stage)
