"""Real router ASGI regression; external services and mutating storage are mocked."""
import os
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
from core.utils.proxy_headers import TrustedProxyHeadersMiddleware, trusted_proxy_networks, origin_tuple
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
        for value in ('1','0','arbitrary','*','0.0.0.0/0','::/0','127.0.0.1,'):
            with self.subTest(value),self.assertRaises(ValueError):trusted_proxy_networks(value)
        self.assertEqual(len(trusted_proxy_networks('127.0.0.1, ::1, 10.2.0.0/24')),3)
        c=TestClient(make_app('10.2.0.2'),client=('10.2.0.2',1))
        for host in ('evil.test, public.test','user@public.test','public.test/path','public.test:bad'):
            self.assertEqual(c.get('/test/url',headers={'x-forwarded-host':host}).status_code,400)
        self.assertEqual(origin_tuple('https://[::1]:443'),('https','::1',443))

    def test_startup_configuration_and_no_outer_uvicorn_rewrite(self):
        from services import settings_service
        for value in ('1', '0', '*'):
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
