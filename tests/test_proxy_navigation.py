"""Real router ASGI regression; external services and mutating storage are mocked."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
os.environ.setdefault('SAFETYREPORT_DATA_DIR', tempfile.mkdtemp(prefix='sr-proxy-test-'))
import unittest
from unittest.mock import patch, Mock, AsyncMock
from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import JSONResponse
from starlette.testclient import TestClient
from core.utils import csrf
from core.utils.proxy_headers import (TrustedProxyHeadersMiddleware, trusted_proxy_networks, origin_tuple,
                                      parse_trusted_proxies, LOOPBACK_NETWORKS)
from web.routers import settings_route, devices_route, community_route, crawl, rating_route, watchlist_route


def make_app(trusted=''):
    app=FastAPI()
    for mod in (settings_route, devices_route, community_route, crawl, rating_route, watchlist_route):
        app.include_router(mod.router)
    @app.get('/test/session')
    def session(request: Request):
        request.session['user']='fixture-admin'
        return {'token':csrf.get_or_create_token(request)}
    @app.get('/test/url')
    def url(request: Request):
        return {'url':str(request.url),'client':request.client.host,'peer':request.scope['safetyreport.connection_peer']}
    app.add_middleware(SessionMiddleware, secret_key='synthetic-test-secret')
    app.add_middleware(TrustedProxyHeadersMiddleware, trusted_proxies=trusted)
    return app


class ProxyTests(unittest.TestCase):
    def post(self, peer, trusted, origin, forwarded=None, token_mode='good', extra=None):
        service=Mock();service.start.return_value={'state':'pending'}
        with TestClient(make_app(trusted), client=(peer,1234), base_url='http://internal.test') as client, patch.object(community_route.cas,'get_service',return_value=service):
            token=client.get('/test/session').json()['token']
            headers={'origin':origin, **(forwarded or {}), **(extra or {})}
            if token_mode!='missing':headers['x-csrf-token']=token if token_mode=='good' else 'wrong'
            response=client.post('/settings/community/start',json={},headers=headers)
            return response,service.start.call_count

    def test_direct_http_and_canonical_origin(self):
        for origin in ('http://internal.test','http://INTERNAL.TEST:80'):
            r,calls=self.post('198.51.100.2','',origin)
            self.assertEqual((r.status_code,calls),(200,1))

    def test_trusted_https_start_and_invalid_tokens(self):
        h={'x-forwarded-proto':'https','x-forwarded-host':'public.test','x-forwarded-for':'203.0.113.2'}
        r,calls=self.post('10.2.0.2','10.2.0.0/24','https://PUBLIC.TEST:443',h)
        self.assertEqual((r.status_code,calls),(200,1))
        for token in ('missing','bad'):
            for trusted in ('','10.2.0.0/24'):
                origin='https://public.test' if trusted else 'http://internal.test'
                r,calls=self.post('10.2.0.2',trusted,origin,h,token)
                self.assertEqual((r.status_code,calls),(403,0))

    def test_untrusted_forgery_and_wrong_origin(self):
        h={'x-forwarded-proto':'https','x-forwarded-host':'evil.test','x-forwarded-for':'10.2.0.2'}
        r,calls=self.post('198.51.100.2','10.2.0.0/24','https://evil.test',h)
        self.assertEqual((r.status_code,calls),(403,0))
        for origin in ('https://evil.test','http://internal.test:81','null','http://internal.test?q=x','http://user@internal.test'):
            r,calls=self.post('10.2.0.2','10.2.0.0/24',origin)
            self.assertEqual((r.status_code,calls),(403,0))
        r,calls=self.post('198.51.100.2','','http://internal.test',extra={'sec-fetch-site':'cross-site'})
        self.assertEqual((r.status_code,calls),(403,0))

    def test_source_peer_and_forwarded_chain(self):
        client=TestClient(make_app('10.0.0.0/8'),client=('10.2.0.2',1234),base_url='http://internal.test')
        r=client.get('/test/url',headers={'x-forwarded-for':'127.0.0.1, 198.51.100.3, 10.3.0.2','x-forwarded-proto':'https','x-forwarded-host':'public.test:8443'})
        self.assertEqual(r.json(),{'url':'https://public.test:8443/test/url','client':'198.51.100.3','peer':['10.2.0.2',1234]})
        forged=TestClient(make_app('10.0.0.0/8'),client=('198.51.100.2',1234),base_url='http://internal.test')
        r=forged.get('/test/url',headers={'x-forwarded-for':'10.2.0.2','x-forwarded-proto':'https','x-forwarded-host':'evil.test'})
        self.assertEqual(r.json()['url'],'http://internal.test/test/url')

    def test_invalid_configuration_and_forwarded_authority(self):
        # 설정 저장용 엄격 검사: 진단은 유지하되 빈 항목(끝 쉼표)은 무시한다.
        for value in ('1','0','arbitrary','nginx','*','0.0.0.0/0','::/0','127.0.0.1, nginx'):
            with self.subTest(value),self.assertRaises(ValueError):trusted_proxy_networks(value)
        self.assertEqual(len(trusted_proxy_networks('127.0.0.1, ::1, 10.2.0.0/24')),3)
        self.assertEqual(trusted_proxy_networks('127.0.0.1,'),trusted_proxy_networks('127.0.0.1'))
        self.assertEqual(trusted_proxy_networks(' , '),())
        c=TestClient(make_app('10.2.0.2'),client=('10.2.0.2',1))
        for host in ('evil.test, public.test','user@public.test','public.test/path','public.test:bad'):
            self.assertEqual(c.get('/test/url',headers={'x-forwarded-host':host}).status_code,400)
        self.assertEqual(origin_tuple('https://[::1]:443'),('https','::1',443))

    def test_startup_configuration_and_no_outer_uvicorn_rewrite(self):
        from services import settings_service
        for value in ('1', '0', '*', 'nginx'):
            with self.assertRaises(ValueError):
                settings_service.web_command({'trusted_proxies': value})
        # Entrypoints explicitly preserve the socket peer for the app boundary.
        self.assertIn('--no-proxy-headers', Path('entrypoint.sh').read_text())
        self.assertEqual(Path('main.py').read_text().count('proxy_headers=False'), 2)
        self.assertIn('proxy_headers=False', Path('scripts/dev/fixture_server.py').read_text())

    def test_json_requirement_remains(self):
        c=TestClient(make_app());token=c.get('/test/session').json()['token']
        r=c.post('/settings/community/start',data={'x':1},headers={'x-csrf-token':token})
        self.assertEqual(r.status_code,403)
        self.assertEqual(r.json()['reason'],'content_type')


FWD={'x-forwarded-proto':'https','x-forwarded-host':'public.test','x-forwarded-for':'203.0.113.9'}


class LocalProxyDefaultTests(unittest.TestCase):
    """trusted_proxies 가 비면 같은 PC 프록시(127.0.0.1, ::1)만 신뢰한다(이전 Uvicorn 기본과 같음)."""
    post=ProxyTests.post

    def test_loopback_proxy_with_empty_setting_posts_and_restores_https(self):
        for peer in ('127.0.0.1','::1'):
            with self.subTest(peer):
                r,calls=self.post(peer,'','https://public.test',FWD)
                self.assertEqual((r.status_code,calls),(200,1),r.text)
                c=TestClient(make_app(''),client=(peer,1234),base_url='http://internal.test')
                body=c.get('/test/url',headers=FWD).json()
                self.assertEqual((body['url'],body['client']),('https://public.test/test/url','203.0.113.9'))
                self.assertEqual(body['peer'],[peer,1234])

    def test_remote_peer_forwarded_headers_ignored_with_empty_setting(self):
        c=TestClient(make_app(''),client=('198.51.100.2',1234),base_url='http://internal.test')
        body=c.get('/test/url',headers=FWD).json()
        self.assertEqual((body['url'],body['client']),('http://internal.test/test/url','198.51.100.2'))
        r,calls=self.post('198.51.100.2','','https://public.test',FWD)
        self.assertEqual((r.status_code,calls),(403,0))
        self.assertEqual(r.json()['reason'],'origin')
        # 루프백이 신뢰 프록시를 거쳐 왔다고 주장해도 원격 peer 는 그대로다.
        r,calls=self.post('198.51.100.2','','https://public.test',{**FWD,'x-forwarded-for':'127.0.0.1'})
        self.assertEqual((r.status_code,calls),(403,0))

    def test_explicit_list_replaces_loopback_default(self):
        c=TestClient(make_app('10.2.0.0/24'),client=('127.0.0.1',1234),base_url='http://internal.test')
        self.assertEqual(c.get('/test/url',headers=FWD).json()['url'],'http://internal.test/test/url')
        r,calls=self.post('127.0.0.1','10.2.0.0/24','https://public.test',FWD)
        self.assertEqual((r.status_code,calls),(403,0))

    def test_issue6_slash_redirect_via_local_proxy_keeps_https_and_query(self):
        c=TestClient(make_app(''),client=('127.0.0.1',42),base_url='http://internal.test')
        r=c.get('/crawl?q=a%2Fb&saved=true',headers=FWD,follow_redirects=False)
        self.assertEqual((r.status_code,r.headers['location']),(307,'https://public.test/crawl/?q=a%2Fb&saved=true'))

    def test_lenient_startup_parse(self):
        cfg=parse_trusted_proxies('')
        self.assertEqual((cfg.networks,cfg.trust_all,cfg.implicit_loopback,cfg.warnings),(LOOPBACK_NETWORKS,False,True,()))
        cfg=parse_trusted_proxies('127.0.0.1,')
        self.assertEqual((len(cfg.networks),cfg.implicit_loopback,cfg.warnings),(1,False,()))
        cfg=parse_trusted_proxies('nginx, 10.2.0.2')
        self.assertEqual([str(n) for n in cfg.networks],['10.2.0.2/32'])
        self.assertEqual(len(cfg.warnings),1);self.assertIn('nginx',cfg.warnings[0])
        cfg=parse_trusted_proxies('nginx')
        self.assertEqual((cfg.networks,cfg.implicit_loopback),(LOOPBACK_NETWORKS,True))
        self.assertEqual(len(cfg.warnings),2)
        cfg=parse_trusted_proxies('*')
        self.assertTrue(cfg.trust_all);self.assertEqual(len(cfg.warnings),1)
        for value in ('1','0'):
            cfg=parse_trusted_proxies(value)
            self.assertEqual((cfg.networks,cfg.implicit_loopback),(LOOPBACK_NETWORKS,True))

    def test_star_trusts_every_peer_like_old_uvicorn(self):
        c=TestClient(make_app('*'),client=('198.51.100.2',1234),base_url='http://internal.test')
        body=c.get('/test/url',headers={**FWD,'x-forwarded-for':'203.0.113.9, 10.0.0.5'}).json()
        self.assertEqual((body['url'],body['client']),('https://public.test/test/url','203.0.113.9'))
        r,calls=self.post('198.51.100.2','*','https://public.test',FWD)
        self.assertEqual((r.status_code,calls),(200,1))

    def test_hostname_entry_is_ignored_and_valid_entries_used(self):
        c=TestClient(make_app('nginx, 10.2.0.2'),client=('10.2.0.2',1234),base_url='http://internal.test')
        self.assertEqual(c.get('/test/url',headers=FWD).json()['url'],'https://public.test/test/url')
        c=TestClient(make_app('nginx, 10.2.0.2'),client=('127.0.0.1',1234),base_url='http://internal.test')
        self.assertEqual(c.get('/test/url',headers=FWD).json()['url'],'http://internal.test/test/url')

    def test_underscore_host_origin(self):
        self.assertEqual(origin_tuple('http://my_pc:6819'),('http','my_pc',6819))
        self.assertEqual(origin_tuple('http://MY_PC:6819/'),('http','my_pc',6819))
        service=Mock();service.start.return_value={'state':'pending'}
        with TestClient(make_app(),client=('192.168.0.20',1234),base_url='http://my_pc:6819') as c, \
                patch.object(community_route.cas,'get_service',return_value=service):
            token=c.get('/test/session').json()['token']
            r=c.post('/settings/community/start',json={},headers={'origin':'http://my_pc:6819','x-csrf-token':token})
            self.assertEqual((r.status_code,service.start.call_count),(200,1),r.text)
            r=c.post('/settings/community/start',json={},headers={'origin':'http://other_pc:6819','x-csrf-token':token})
            self.assertEqual(r.status_code,403)
        # 신뢰 프록시가 넘긴 밑줄 호스트도 받는다(Docker 컨테이너 이름).
        c=TestClient(make_app('10.2.0.2'),client=('10.2.0.2',1),base_url='http://internal.test')
        self.assertEqual(c.get('/test/url',headers={'x-forwarded-host':'safety_report:6819'}).json()['url'],
                         'http://safety_report:6819/test/url')


_CREATE_APP_PROBE = """
import json, sys
sys.path.insert(0, {root!r})
import main
app = main.get_app()
names = [m.cls.__name__ for m in app.user_middleware]
from core.utils import logger
print(json.dumps({{"outer": names[0]}}))
"""


class CreateAppTrustedProxiesTests(unittest.TestCase):
    """예전에 동작하던 trusted_proxies 값으로 서버 시작이 실패하지 않는다(경고만)."""
    def test_create_app_accepts_legacy_values_with_warning(self):
        root=str(Path(__file__).resolve().parents[1])
        cases={'*':True,'nginx':True,'127.0.0.1,':False,'nginx, 172.18.0.0/16':True,'':False}
        for value,warns in cases.items():
            with self.subTest(value=value):
                data_dir=tempfile.mkdtemp(prefix='sr-proxy-app-')
                Path(data_dir,'config.ini').write_text('[SETTINGS]\ntrusted_proxies = '+value+'\n',encoding='utf-8')
                env=dict(os.environ,SAFETYREPORT_DATA_DIR=data_dir)
                result=subprocess.run([sys.executable,'-c',_CREATE_APP_PROBE.format(root=root)],cwd=root,env=env,
                                      capture_output=True,text=True,timeout=120)
                self.assertEqual(result.returncode,0,result.stderr[-2000:])
                self.assertEqual(json.loads(result.stdout.strip().splitlines()[-1]),{'outer':'TrustedProxyHeadersMiddleware'})
                self.assertEqual('[proxy]' in result.stdout,warns,result.stdout[-2000:])


class NavigationTests(unittest.TestCase):
    def setUp(self):
        self.client=TestClient(make_app('10.2.0.2'),client=('10.2.0.2',42),base_url='http://internal.test')
        self.forwarded={'x-forwarded-proto':'https','x-forwarded-host':'public.test'}

    def test_old_links_redirect_once_with_external_https_and_query(self):
        for name in ('crawl','rating','watchlist','settings','devices'):
            r=self.client.get('/'+name+'?q=a%2Fb&saved=true',headers=self.forwarded,follow_redirects=False)
            self.assertEqual(r.status_code,307)
            self.assertEqual(r.headers['location'],'https://public.test/'+name+'/?q=a%2Fb&saved=true')

    def test_direct_five_routes_without_additional_redirect(self):
        from core.utils.templating import templates
        from services import settings_service, data_service, community_gate, ws_manager
        with patch.object(templates,'TemplateResponse',side_effect=lambda *a,**k:JSONResponse({'template':a[1]})), \
             patch.object(settings_service,'view_values',return_value={}), \
             patch.object(community_gate,'check_for_request',return_value={'state':'ready'}), \
             patch.object(data_service,'get_unrated_records',return_value=[]), \
             patch.object(data_service,'get_all_watchlist',return_value=[]), \
             patch.object(devices_route.database,'get_all_api_keys',return_value=[]), \
             patch.object(ws_manager.ws_manager,'get_connected_clients',return_value=[]):
            for name in ('crawl','rating','watchlist','settings','devices'):
                r=self.client.get('/'+name+'/',headers=self.forwarded,follow_redirects=False)
                self.assertEqual(r.status_code,200,(name,r.text))
                self.assertNotIn('location',r.headers)

    def test_settings_upload_and_delete_post_use_303(self):
        with patch.object(settings_route.settings_service,'apply') as apply:
            r=self.client.post('/settings/save',data={'trusted_proxies':''},follow_redirects=False)
            self.assertEqual((r.status_code,r.headers['location']),(303,'/settings/?saved=true'))
            self.assertEqual(apply.call_count,1)
            r=self.client.post('/settings/save',data={'trusted_proxies':'1'},follow_redirects=False)
            self.assertEqual(r.status_code,400);self.assertEqual(apply.call_count,1)
        r=self.client.post('/settings/upload_json',files={'file':('bad.txt',b'')},follow_redirects=False)
        self.assertEqual((r.status_code,r.headers['location']),(303,'/settings/?error=invalid_file'))
        with patch.object(settings_route.settings_service,'validate_google_credential'),patch.object(settings_route.settings_service,'save_google_credential'):
            r=self.client.post('/settings/upload_json',files={'file':('fixture.json',b'{}')},follow_redirects=False)
            self.assertEqual((r.status_code,r.headers['location']),(303,'/settings/?saved=true'))
        from services.ws_manager import ws_manager
        with patch.object(devices_route.database,'delete_api_key'),patch.object(ws_manager,'revoke_api_key',new=AsyncMock()):
            r=self.client.post('/devices/api-keys/delete',data={'key':'fixture-key'},follow_redirects=False)
            self.assertEqual((r.status_code,r.headers['location']),(303,'/devices/'))

    def test_active_menu_and_canonical_links_in_real_template(self):
        from core.utils.templating import templates
        for name in ('crawl','rating','watchlist','settings','devices'):
            request=Request({'type':'http','scheme':'http','path':'/'+name+'/','headers':[(b'host',b'test')],'query_string':b'','session':{}})
            rendered=templates.env.get_template('base.html').render(request=request,csrf_token='fixture')
            self.assertIn(f'href="/{name}/" class="active"',rendered)
        text=Path('web/templates/settings.html').read_text()
        self.assertIn("savedUrl.searchParams.delete('saved')",text)
        self.assertIn('savedUrl.pathname + savedUrl.search + savedUrl.hash',text)
