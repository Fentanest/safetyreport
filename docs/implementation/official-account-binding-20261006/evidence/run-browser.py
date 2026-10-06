import subprocess, sys, time, urllib.request
from pathlib import Path
root=Path(__file__).resolve().parents[4]
run=root/'.agent-runs/official-binding-rc'
evidence=Path(__file__).resolve().parent
run.mkdir(parents=True,exist_ok=True)
engine=sys.argv[1]
with (run/f'browser-server-{engine}-verified.log').open('w') as log:
 server=subprocess.Popen([str(root/'.venv/bin/python'),str(evidence/'browser_server.py')],stdout=log,stderr=subprocess.STDOUT)
 try:
  for i in range(100):
   if server.poll() is not None: raise RuntimeError('fixture exited '+str(server.returncode))
   try:
    with urllib.request.urlopen('http://127.0.0.1:18806/health',timeout=1) as r:
     assert r.status==200
     break
   except OSError: time.sleep(.1)
  else: raise RuntimeError('fixture not ready')
  subprocess.run(['node',str(evidence/'browser-check.cjs'),engine],check=True,timeout=100)
 finally:
  server.terminate()
  try: server.wait(timeout=10)
  except subprocess.TimeoutExpired:
   server.kill();server.wait()
