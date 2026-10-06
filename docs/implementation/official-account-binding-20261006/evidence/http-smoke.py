import sys, json
import requests
base=sys.argv[1]
s=requests.Session()
checks=[]
for path,mime in [('/health','application/json'),('/login','text/html'),('/static/ui/official-account-settings.js','javascript'),('/static/ui/official-account-retry.js','javascript'),('/static/logo.png','image/png')]:
 r=s.get(base+path,timeout=15)
 assert r.status_code==200,(path,r.status_code)
 assert mime in r.headers['content-type'],(path,r.headers['content-type'])
 checks.append({'path':path,'status':r.status_code,'mime':r.headers['content-type']})
r=s.post(base+'/login',data={'username':'fixture-admin','password':'fixture-pass-1234'},allow_redirects=False,timeout=15)
assert r.status_code==303,(r.status_code,r.text[:100])
r=s.get(base+'/settings/',timeout=15)
assert r.status_code==200
assert '/login' not in r.url
checks.append({'login':303,'authenticated_gate_path':r.url.removeprefix(base),'status':r.status_code})
print(json.dumps(checks,indent=2))
