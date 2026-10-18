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
                                      parse_trusted_proxies, AUTO_NETWORKS, validate_trusted_proxies_setting,
                                      set_trusted_proxies, get_trusted_proxy_config,
                                      ProxyConfigurationError)
from web.routers import settings_route, devices_route, community_route, crawl, rating_route, watchlist_route


def make_app(trusted=''):
    set_trusted_proxies(trusted)
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
    app.add_middleware(TrustedProxyHeadersMiddleware)
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

    def test_untrusted_forwarded_host_is_legacy_csrf_candidate_and_wrong_origin_rejected(self):
        # 재설계: 비신뢰 XFH도 구형 브라우저 Origin 후보이며 토큰은 계속 필수.
        h={'x-forwarded-proto':'https','x-forwarded-host':'evil.test','x-forwarded-for':'10.2.0.2'}
        r,calls=self.post('198.51.100.2','10.2.0.0/24','https://evil.test',h)
        self.assertEqual((r.status_code,calls),(200,1))
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
            # 재설계: 잘못된 XFH만 무시하고 요청과 원래 Host는 유지한다.
            response=c.get('/test/url',headers={'x-forwarded-host':host})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['url'],'http://testserver/test/url')
        self.assertEqual(origin_tuple('https://[::1]:443'),('https','::1',443))

    def test_startup_configuration_and_no_outer_uvicorn_rewrite(self):
        from services import settings_service
        with patch.object(settings_service,'_stored_trusted_proxies',return_value=''):
            for value in ('1', '0', 'nginx', '0.0.0.0/0'):
                with self.subTest(value),self.assertRaises(ValueError):
                    settings_service.web_command({'trusted_proxies': value})
            # '*' 는 시작 때와 같은 의미로 저장하되 경고를 돌려준다(2026-10-16 Opus 결정).
            command=settings_service.web_command({'trusted_proxies': ' * '})
            self.assertEqual(dict(((s,k),v) for s,k,v in command.values)[('SETTINGS','trusted_proxies')],'*')
            self.assertEqual(len(command.warnings),1)
            self.assertEqual(settings_service.web_command({'trusted_proxies': '127.0.0.1'}).warnings,())
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
    """재설계: 빈 값은 auto 대역 신뢰, 출처 확인은 프록시 신뢰와 분리한다."""
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
        self.assertEqual((r.status_code,calls),(200,1))
        # 재설계: XFH는 CSRF 후보로만 사용한다. 전달 IP/HTTPS 비신뢰는 그대로다.
        r,calls=self.post('198.51.100.2','','https://public.test',{**FWD,'x-forwarded-for':'127.0.0.1'})
        self.assertEqual((r.status_code,calls),(200,1))

    def test_explicit_list_replaces_auto_default_but_does_not_block_csrf(self):
        # 재설계: 명시 목록 밖이어도 XFH와 Origin이 같고 토큰이 맞으면 통과.
        c=TestClient(make_app('10.2.0.0/24'),client=('127.0.0.1',1234),base_url='http://internal.test')
        self.assertEqual(c.get('/test/url',headers=FWD).json()['url'],'http://internal.test/test/url')
        r,calls=self.post('127.0.0.1','10.2.0.0/24','https://public.test',FWD)
        self.assertEqual((r.status_code,calls),(200,1))

    def test_slash_redirect_via_local_proxy_is_relative_and_keeps_query(self):
        # 재설계: 브라우저 HTTPS를 유지하도록 같은 Host의 Location은 상대 경로.
        c=TestClient(make_app(''),client=('127.0.0.1',42),base_url='http://internal.test')
        r=c.get('/crawl?q=a%2Fb&saved=true',headers=FWD,follow_redirects=False)
        self.assertEqual((r.status_code,r.headers['location']),(307,'/crawl/?q=a%2Fb&saved=true'))

    def test_lenient_startup_parse(self):
        # 재설계: 빈 값·유효 항목 없는 설정은 루프백 대신 auto 대역으로 대체.
        cfg=parse_trusted_proxies('')
        self.assertEqual((cfg.networks,cfg.trust_all,cfg.implicit_auto,cfg.warnings),(AUTO_NETWORKS,False,True,()))
        cfg=parse_trusted_proxies('127.0.0.1,')
        self.assertEqual((len(cfg.networks),cfg.implicit_auto,cfg.warnings),(1,False,()))
        cfg=parse_trusted_proxies('nginx, 10.2.0.2')
        self.assertEqual([str(n) for n in cfg.networks],['10.2.0.2/32'])
        self.assertEqual(len(cfg.warnings),1);self.assertIn('nginx',cfg.warnings[0])
        cfg=parse_trusted_proxies('nginx')
        self.assertEqual((cfg.networks,cfg.implicit_auto),(AUTO_NETWORKS,True))
        self.assertEqual(len(cfg.warnings),2)
        cfg=parse_trusted_proxies('*')
        self.assertTrue(cfg.trust_all);self.assertEqual(len(cfg.warnings),1)
        for value in ('1','0'):
            cfg=parse_trusted_proxies(value)
            self.assertEqual((cfg.networks,cfg.implicit_auto),(AUTO_NETWORKS,True))

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



class SettingsSaveTrustedProxiesTests(unittest.TestCase):
    """설정 저장: '*' 는 경고와 함께 저장, 새로 넣은 해석 불가 항목은 400, 바꾸지 않은 기존 값은 막지 않는다."""

    def test_validator(self):
        self.assertEqual(len(validate_trusted_proxies_setting('*')),1)
        self.assertEqual(len(validate_trusted_proxies_setting('*, 10.0.0.1')),1)
        self.assertEqual(validate_trusted_proxies_setting('127.0.0.1, ::1,'),())
        for value in ('nginx','1','::/0','10.0.0.1, my_proxy'):
            with self.subTest(value),self.assertRaises(ProxyConfigurationError):
                validate_trusted_proxies_setting(value,'')
        # 저장된 값과 같으면(칸을 바꾸지 않음) 통과하고 시작 때와 같은 경고를 돌려준다.
        self.assertTrue(validate_trusted_proxies_setting('nginx','nginx'))
        self.assertTrue(validate_trusted_proxies_setting(' nginx , 10.0.0.1','nginx,10.0.0.1,'))
        with self.assertRaises(ProxyConfigurationError):
            validate_trusted_proxies_setting('nginx, other','nginx')

    def _save(self, stored, submitted):
        from services import settings_service
        client=TestClient(make_app(),client=('192.168.0.20',1),base_url='http://internal.test')
        with patch.object(settings_service,'_stored_trusted_proxies',return_value=stored), \
             patch.object(settings_route.settings_service,'apply') as apply:
            r=client.post('/settings/save',data={'trusted_proxies':submitted,'telegram_token':'changed'},follow_redirects=False)
        return r,apply

    def test_unchanged_legacy_value_does_not_block_other_settings(self):
        for stored in ('nginx','1','nginx, 172.18.0.2','0.0.0.0/0'):
            with self.subTest(stored):
                r,apply=self._save(stored,stored)
                self.assertEqual((r.status_code,r.headers.get('location')),(303,'/settings/?saved=true'),r.text)
                self.assertEqual(apply.call_count,1)
                values={(s,k):v for s,k,v in apply.call_args.args[0].values}
                self.assertEqual(values[('SETTINGS','trusted_proxies')],stored.strip())
                self.assertEqual(values[('TELEGRAM','telegram_token')],'changed')

    def test_star_saved_and_new_invalid_rejected(self):
        r,apply=self._save('','*')
        self.assertEqual((r.status_code,apply.call_count),(303,1))
        self.assertTrue(apply.call_args.args[0].warnings)
        r,apply=self._save('nginx','nginx2')
        self.assertEqual((r.status_code,apply.call_count),(400,0))
        self.assertIn('nginx2',r.json()['detail'])

    def test_settings_page_shows_wildcard_warning(self):
        from services import settings_service, community_gate
        real=settings_service.view_values
        def values():
            data=real();data['trusted_proxies']='*'
            data['trusted_proxies_warnings']=settings_service._proxy_warnings('*')
            return data
        from starlette.staticfiles import StaticFiles
        app=make_app();app.mount('/static',StaticFiles(directory='web/static'),name='static')
        client=TestClient(app,client=('192.168.0.20',1),base_url='http://internal.test')
        client.get('/test/session')
        with patch.object(settings_service,'view_values',side_effect=values), \
             patch.object(community_gate,'check_for_request',return_value={'state':'ready'}):
            r=client.get('/settings/')
        self.assertEqual(r.status_code,200,r.text[-500:])
        self.assertIn('sr-proxy-warning',r.text)
        self.assertIn('모든 연결의 X-Forwarded-* 를 신뢰합니다',r.text)
        self.assertEqual(settings_service._proxy_warnings('127.0.0.1'),())


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

    def test_old_links_redirect_once_with_relative_location_and_query(self):
        for name in ('crawl','rating','watchlist','settings','devices'):
            r=self.client.get('/'+name+'?q=a%2Fb&saved=true',headers=self.forwarded,follow_redirects=False)
            self.assertEqual(r.status_code,307)
            # 재설계: 같은 Host 리다이렉트는 scheme과 무관한 상대 Location.
            self.assertEqual(r.headers['location'],'/'+name+'/?q=a%2Fb&saved=true')

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


class ProxyRedesignTests(unittest.TestCase):
    post = ProxyTests.post

    def test_fetch_metadata_precedes_proxy_origin_but_never_token(self):
        for site in ('same-origin', 'SAME-ORIGIN', 'none'):
            with self.subTest(site=site):
                r, calls = self.post('172.19.0.1', '10.20.10.3', 'https://internal.test',
                                     extra={'sec-fetch-site': site})
                self.assertEqual((r.status_code, calls), (200, 1))
                for token in ('bad', 'missing'):
                    r, calls = self.post('172.19.0.1', '10.20.10.3', 'https://internal.test',
                                         token_mode=token, extra={'sec-fetch-site': site})
                    self.assertEqual((r.status_code, calls, r.json()['reason']), (403, 0, 'csrf'))
        r, calls = self.post('172.19.0.1', '', 'http://internal.test',
                             extra={'sec-fetch-site': 'CROSS-SITE'})
        self.assertEqual((r.status_code, calls, r.json()['reason']), (403, 0, 'cross_site'))

    def test_same_site_and_unknown_metadata_keep_strict_origin(self):
        for site in ('same-site', 'unknown', ''):
            for origin in ('http://internal.test:81', 'https://internal.test', 'http://sub.internal.test', 'null'):
                with self.subTest(site=site, origin=origin):
                    r, calls = self.post('172.19.0.1', 'none', origin, extra={'sec-fetch-site': site})
                    self.assertEqual((r.status_code, calls, r.json()['reason']), (403, 0, 'origin'))
            r, calls = self.post('172.19.0.1', 'none', 'http://internal.test:80',
                                 extra={'sec-fetch-site': site})
            self.assertEqual((r.status_code, calls), (200, 1))

    def test_legacy_origin_normalizes_scheme_and_default_ports(self):
        for host, origin in (
            ('internal.test', 'https://internal.test'),
            ('internal.test:443', 'https://INTERNAL.TEST:443'),
            ('internal.test:80', 'https://internal.test'),
            ('internal.test:6819', 'http://internal.test:6819'),
            ('internal.test:6819', 'https://internal.test:6819'),
            ('[fd00::1]:443', 'https://[fd00::1]'),
        ):
            with self.subTest(host=host, origin=origin):
                r, calls = self.post('8.8.8.8', 'none', origin, extra={'host': host})
                self.assertEqual((r.status_code, calls), (200, 1))
        for origin in ('https://other.test', 'null', 'https://internal.test:8443'):
            r, calls = self.post('8.8.8.8', 'none', origin)
            self.assertEqual((r.status_code, calls, r.json()['reason']), (403, 0, 'origin'))

    def test_legacy_original_hosts_survive_rewrite_and_forwarded_lists(self):
        for trusted in ('none', 'auto'):
            for origin in ('http://internal.test', 'https://public.test', 'https://other.test'):
                with self.subTest(trusted=trusted, origin=origin):
                    r, calls = self.post('172.19.0.1', trusted, origin,
                                         {'x-forwarded-host': 'public.test, other.test'})
                    self.assertEqual((r.status_code, calls), (200, 1))
            r, calls = self.post('172.19.0.1', trusted, 'http://internal.test',
                                 {'x-forwarded-host': 'public.test', 'x-forwarded-proto': 'https'})
            self.assertEqual((r.status_code, calls), (200, 1))
        r, calls = self.post('8.8.8.8', 'none', 'https://public.test',
                             {'host': '127.0.0.1:6819', 'x-forwarded-host': 'public.test'})
        self.assertEqual((r.status_code, calls), (200, 1))

    def test_no_source_headers_still_requires_token(self):
        request = Request({'type': 'http', 'scheme': 'http', 'path': '/', 'headers': [],
                           'session': {csrf.SESSION_KEY: 'fixture-token'}})
        self.assertIsNone(csrf.verify_token(request, 'fixture-token'))
        self.assertEqual(csrf.verify_token(request, 'wrong'), 'csrf')
        self.assertEqual(csrf.verify_token(request, None), 'csrf')

    def test_auto_networks_none_and_explicit_combinations(self):
        peers = ('127.2.3.4', '::1', '10.20.10.3', '172.19.0.1', '192.168.3.4',
                 '100.64.1.2', '169.254.3.4', 'fd00::1', 'fe80::1', '::ffff:172.19.0.1')
        for setting in ('', 'auto'):
            config = parse_trusted_proxies(setting)
            for peer in peers:
                with self.subTest(setting=setting, peer=peer):
                    self.assertTrue(config.trusts(peer))
                    c = TestClient(make_app(setting), client=(peer, 1), base_url='http://internal.test')
                    self.assertEqual(c.get('/test/url', headers=FWD).json()['url'], 'https://public.test/test/url')
            self.assertFalse(config.trusts('8.8.8.8'))
        self.assertFalse(parse_trusted_proxies('none').trusts('127.0.0.1'))
        self.assertFalse(parse_trusted_proxies('none').trusts('::1'))
        config = parse_trusted_proxies('auto, 203.0.113.5')
        self.assertTrue(config.trusts('203.0.113.5'))
        self.assertTrue(config.trusts('100.64.1.2'))
        self.assertFalse(config.trusts('203.0.113.6'))
        config = parse_trusted_proxies('none, *, auto, 10.0.0.1')
        self.assertEqual(config.networks, ())
        self.assertFalse(config.trust_all)
        self.assertEqual(len(config.warnings), 1)
        for value in ('0.0.0.0/0', '::/0'):
            config = parse_trusted_proxies(value)
            self.assertEqual(config.networks, AUTO_NETWORKS)
            self.assertFalse(config.trusts('8.8.8.8'))
            self.assertEqual(len(config.warnings), 2)

    def test_mapped_xff_walks_trust_boundary_as_ipv4(self):
        c = TestClient(make_app(), client=('::ffff:172.19.0.1', 1), base_url='http://internal.test')
        body = c.get('/test/url', headers={'x-forwarded-for':
                     '127.0.0.1, ::ffff:203.0.113.7, ::ffff:10.0.0.2'}).json()
        self.assertEqual(body['client'], '203.0.113.7')
        self.assertEqual(body['peer'], ['::ffff:172.19.0.1', 1])

    def test_invalid_headers_are_ignored_independently(self):
        c = TestClient(make_app(), client=('172.19.0.1', 1), base_url='http://internal.test')
        for host_headers in (
            [('x-forwarded-host', 'one.test'), ('x-forwarded-host', 'two.test')],
            [('x-forwarded-host', 'one.test, two.test')],
            [('x-forwarded-host', 'public.test/')],
        ):
            r = c.get('/test/url', headers=host_headers + [('x-forwarded-proto', 'https'),
                                                          ('x-forwarded-for', '203.0.113.7')])
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), {'url': 'https://internal.test/test/url', 'client': '203.0.113.7',
                                        'peer': ['172.19.0.1', 1]})
        for proto_headers in (
            [('x-forwarded-proto', 'https'), ('x-forwarded-proto', 'http')],
            [('x-forwarded-proto', 'https,http')], [('x-forwarded-proto', 'ftp')],
        ):
            r = c.get('/test/url', headers=proto_headers + [('x-forwarded-host', 'public.test'),
                                                           ('x-forwarded-for', 'not-an-ip')])
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json(), {'url': 'http://public.test/test/url', 'client': '172.19.0.1',
                                        'peer': ['172.19.0.1', 1]})

    def test_invalid_websocket_headers_do_not_close_connection(self):
        from fastapi import WebSocket
        app = make_app()
        @app.websocket('/test/ws')
        async def echo(ws: WebSocket):
            await ws.accept()
            await ws.send_json({'scheme': ws.scope['scheme'], 'host': ws.headers['host'],
                                'client': ws.client.host})
            await ws.close()
        c = TestClient(app, client=('172.19.0.1', 1))
        with c.websocket_connect('/test/ws', headers={'x-forwarded-host': 'one.test,two.test',
                                                      'x-forwarded-proto': 'https',
                                                      'x-forwarded-for': 'invalid'}) as ws:
            self.assertEqual(ws.receive_json(), {'scheme': 'wss', 'host': 'testserver', 'client': '172.19.0.1'})

    def test_warnings_once_per_peer_bounded_and_no_header_values(self):
        from core.utils import proxy_headers, logger
        with patch.object(proxy_headers, '_warned_peers', set()), \
             patch.object(proxy_headers, '_WARNING_PEER_LIMIT', 2), \
             patch.object(logger.LoggerFactory, 'logbot') as log:
            c = TestClient(make_app(), client=('8.8.8.8', 1))
            for _ in range(3):
                c.get('/test/url', headers={'x-forwarded-host': 'secret.test'})
            self.assertEqual(log.warning.call_count, 1)
            c = TestClient(make_app(), client=('172.19.0.1', 1))
            for _ in range(3):
                c.get('/test/url', headers={'x-forwarded-for': 'secret-token'})
            self.assertEqual(log.warning.call_count, 2)
            c = TestClient(make_app(), client=('8.8.4.4', 1))
            c.get('/test/url', headers=FWD)
            self.assertEqual(log.warning.call_count, 2)
            self.assertEqual(len(proxy_headers._warned_peers), 2)
            messages = str(log.warning.call_args_list)
            self.assertIn('[proxy]', messages)
            self.assertNotIn('secret', messages)

    def test_admin_post_reproduces_205_with_auto_and_obsolete_setting(self):
        import main
        for setting, scheme in (('', 'https'), ('10.20.10.3', 'http')):
            set_trusted_proxies(setting)
            app = FastAPI()
            @app.get('/test/session')
            def session(request: Request):
                request.session['admin_logged_in'] = True
                return {'token': csrf.get_or_create_token(request)}
            @app.post('/test/admin')
            def admin(request: Request):
                return {'scheme': request.url.scheme}
            app.middleware('http')(main.inject_version_middleware)
            app.add_middleware(SessionMiddleware, secret_key='fixture-secret')
            app.add_middleware(TrustedProxyHeadersMiddleware)
            c = TestClient(app, client=('172.19.0.1', 1), base_url='http://safetyreport.fentanest.com')
            token = c.get('/test/session').json()['token']
            headers = {'x-forwarded-proto': 'https', 'origin': 'https://safetyreport.fentanest.com',
                       'sec-fetch-site': 'same-origin', 'x-csrf-token': token}
            r = c.post('/test/admin', headers=headers)
            self.assertEqual((r.status_code, r.json()), (200, {'scheme': scheme}))
            headers['x-csrf-token'] = 'wrong'
            r = c.post('/test/admin', headers=headers)
            self.assertEqual((r.status_code, r.json()['reason']), (403, 'csrf'))
            headers.pop('x-csrf-token')
            r = c.post('/test/admin', headers=headers, data={'_csrf_token': token})
            self.assertEqual((r.status_code, r.json()), (200, {'scheme': scheme}))

    def test_none_auto_settings_validation_and_save(self):
        helper = SettingsSaveTrustedProxiesTests()
        for previous in ('', 'none, 10.0.0.1'):
            r, apply = helper._save(previous, 'none, 10.0.0.1')
            self.assertEqual((r.status_code, apply.call_count), (400, 0))
        for value in ('auto', 'none', 'auto, 203.0.113.5'):
            r, apply = helper._save('', value)
            self.assertEqual((r.status_code, r.headers['location'], apply.call_count),
                             (303, '/settings/?saved=true', 1))

    def test_relative_redirects_without_trust_and_external_destinations_unchanged(self):
        from starlette.responses import RedirectResponse
        app = make_app('none')
        @app.get('/test/redirect')
        def redirect(target: str):
            return RedirectResponse(target, status_code=302)
        c = TestClient(app, client=('172.19.0.1', 1), base_url='http://internal.test')
        r = c.get('/crawl?q=a%2Fb&saved=true', headers=FWD, follow_redirects=False)
        self.assertEqual((r.status_code, r.headers['location']), (307, '/crawl/?q=a%2Fb&saved=true'))
        for target, expected in (
            ('http://internal.test/path?q=a%2Fb#part', '/path?q=a%2Fb#part'),
            ('https://INTERNAL.TEST:443/path', '/path'),
            ('https://other.test/path', 'https://other.test/path'),
            ('http://internal.test:8000/path', 'http://internal.test:8000/path'),
            ('//other.test/path', '//other.test/path'),
            ('http://internal.test//other.test/path', 'http://internal.test//other.test/path'),
        ):
            r = c.get('/test/redirect', params={'target': target}, follow_redirects=False)
            self.assertEqual((r.status_code, r.headers['location']), (302, expected))

    def test_rendered_scripts_are_relative_and_settings_show_socket_peer(self):
        from core.utils.templating import templates
        from services import community_gate, settings_service
        import re
        app = make_app('none')
        @app.get('/test/table')
        def table(request: Request):
            return templates.TemplateResponse(request, 'data_table.html', {'records': [], 'table_id': 'fixture'})
        c = TestClient(app, client=('172.19.0.1', 1), base_url='http://internal.test')
        with patch.object(community_gate, 'check_for_request', return_value={'state': 'ready'}), \
             patch.object(settings_service, 'view_values', return_value={}):
            for path, expected in (('/settings/', ['official-account-settings']),
                                   ('/test/table', ['list-predicates', 'data-table-cells', 'data-table'])):
                r = c.get(path, headers=FWD)
                self.assertEqual(r.status_code, 200)
                for name in expected:
                    self.assertIn(f'src="/static/ui/{name}.js"', r.text)
                self.assertNotRegex(r.text, r'<script[^>]+src=["\']https?://[^"\']*/static/')
                if path == '/settings/':
                    self.assertIn('지금 이 화면 연결: 172.19.0.1 — 신뢰 안 함(전달 헤더 무시)', r.text)
                    self.assertIn('placeholder="auto"', r.text)
                    self.assertNotIn('변경 후 서버 재시작 필요', r.text)
            set_trusted_proxies('auto')
            r = c.get('/settings/', headers=FWD)
            self.assertIn('지금 이 화면 연결: 172.19.0.1 — 신뢰함', r.text)
