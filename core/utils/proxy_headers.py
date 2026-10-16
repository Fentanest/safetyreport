"""Restore external URLs once, before sessions/auth/CSRF, using the socket peer.

Run Uvicorn with proxy_headers=False / --no-proxy-headers. A scope whose client
has already been rewritten cannot establish the original connection's trust.

trusted_proxies (2026-10-16):
- empty → loopback only (127.0.0.1, ::1), the same default Uvicorn used before
  (forwarded_allow_ips=127.0.0.1), so a reverse proxy on the same PC keeps working.
- an explicit list replaces that default (like Uvicorn's forwarded_allow_ips).
- startup never fails on this value: blank items are skipped, ``*`` trusts every
  peer (old Uvicorn meaning) with a warning, unparsable items (host names etc.)
  are skipped with a warning. The settings form still rejects them (strict check).
"""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse


class ProxyConfigurationError(ValueError):
    pass


LOOPBACK_NETWORKS = (ipaddress.ip_network('127.0.0.1/32'), ipaddress.ip_network('::1/128'))


def _items(value) -> list[str]:
    return [item.strip() for item in (value or '').split(',') if item.strip()]


def trusted_proxy_networks(value: str):
    """Strict check for the settings form: explicit IPs/CIDRs only (blank items ignored).

    Empty means "no explicit list" (the middleware then trusts loopback only).
    Booleans, host names, ``*`` and whole-address-space CIDRs are rejected.
    """
    result = []
    for item in _items(value):
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise ProxyConfigurationError(f'신뢰할 프록시 IP 항목 {item!r} 을 쓸 수 없습니다. 프록시가 서버에 접속하는 '
                                          'IP 또는 CIDR 을 쉼표로 구분해 적으세요(1·0·*·호스트 이름 불가).') from exc
        if network.prefixlen == 0:
            raise ProxyConfigurationError(f'신뢰할 프록시 IP 항목 {item!r} 은 모든 주소를 신뢰하므로 쓸 수 없습니다.')
        result.append(network)
    return tuple(result)


@dataclass(frozen=True)
class TrustedProxyConfig:
    networks: tuple
    trust_all: bool = False
    implicit_loopback: bool = False
    warnings: tuple = ()


def parse_trusted_proxies(value: str) -> TrustedProxyConfig:
    """Lenient parse used at startup. Never raises; problems become warnings."""
    networks, warnings, trust_all = [], [], False
    items = _items(value)
    for item in items:
        if item == '*':
            trust_all = True
            warnings.append("trusted_proxies '*': 모든 연결의 X-Forwarded-* 를 신뢰합니다(이전 Uvicorn 동작). "
                            "외부에서 직접 접속할 수 있으면 주소·HTTPS 정보를 위조할 수 있으니 실제 프록시 IP/CIDR 로 바꾸세요.")
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError:
            warnings.append(f"trusted_proxies 항목 {item!r} 은 IP/CIDR 이 아니라 무시합니다"
                            "(호스트 이름은 지원하지 않음 — 프록시가 서버에 접속하는 IP 또는 CIDR 을 적으세요).")
            continue
        if network.prefixlen == 0:
            warnings.append(f"trusted_proxies 항목 {item!r} 은 모든 주소를 신뢰합니다. 실제 프록시 IP/CIDR 로 좁히세요.")
        networks.append(network)
    implicit = not networks and not trust_all
    if implicit:
        if items:
            warnings.append('trusted_proxies 에 쓸 수 있는 항목이 없어 기본값(127.0.0.1, ::1 만 신뢰)을 씁니다.')
        networks = list(LOOPBACK_NETWORKS)
    return TrustedProxyConfig(tuple(networks), trust_all, implicit, tuple(warnings))


def origin_tuple(value: str):
    """Strict origin tuple, with default ports and DNS case normalized."""
    try:
        if re.search(r'[\s\\%]', value):
            return None
        parts = urlsplit(value)
        if (parts.scheme not in ('http', 'https') or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.path not in ('', '/') or parts.query or parts.fragment):
            return None
        host = parts.hostname.lower()
        if ':' in host:
            host = str(ipaddress.IPv6Address(host))
        elif not re.fullmatch(r'[a-z0-9._-]+', host):
            return None
        port = parts.port
        if parts.netloc.endswith(':') or port == 0:
            return None
        return parts.scheme, host, port or (443 if parts.scheme == 'https' else 80)
    except ValueError:
        return None


class TrustedProxyHeadersMiddleware:
    def __init__(self, app, trusted_proxies: str = ''):
        self.app = app
        self.config = parse_trusted_proxies(trusted_proxies)
        self.networks = self.config.networks

    def _trusted(self, host):
        if self.config.trust_all:
            return True
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return False
        return any(address in network for network in self.networks)

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        peer = scope.get('client')
        scope = dict(scope)
        scope['safetyreport.connection_peer'] = peer
        if not peer or not self._trusted(peer[0]):
            return await self.app(scope, receive, send)
        headers = list(scope['headers'])
        forwarded = {}
        for key in (b'x-forwarded-proto', b'x-forwarded-host', b'x-forwarded-for'):
            values = [v.decode('latin1').strip() for k, v in headers if k == key]
            # URL headers must be one authoritative value, not an ambiguous chain.
            if key != b'x-forwarded-for' and (len(values) > 1 or any(',' in v for v in values)):
                return await self._invalid(scope, receive, send)
            forwarded[key] = ','.join(values)
        proto = forwarded[b'x-forwarded-proto']
        host = forwarded[b'x-forwarded-host']
        if proto and proto not in ('http', 'https'):
            return await self._invalid(scope, receive, send)
        if host and origin_tuple((proto or scope['scheme'].replace('ws', 'http')) + '://' + host) is None:
            return await self._invalid(scope, receive, send)
        if proto:
            scope['scheme'] = proto.replace('http', 'ws') if scope['type'] == 'websocket' else proto
        if host:
            scope['headers'] = [(k, v) for k, v in headers if k != b'host'] + [(b'host', host.encode('latin1'))]
        chain = forwarded[b'x-forwarded-for']
        if chain:
            # Walk from our socket peer towards the client. Do not trust a claimed
            # leftmost address behind an untrusted intermediary.
            hosts = [part.strip() for part in chain.split(',')]
            try:
                for part in hosts:
                    ipaddress.ip_address(part)
            except ValueError:
                return await self._invalid(scope, receive, send)
            for part in reversed(hosts):
                scope['client'] = (part, 0)
                if not self._trusted(part):
                    break
        return await self.app(scope, receive, send)

    @staticmethod
    async def _invalid(scope, receive, send):
        if scope['type'] == 'websocket':
            await send({'type': 'websocket.close', 'code': 1008})
        else:
            await PlainTextResponse('Invalid trusted proxy headers', status_code=400)(scope, receive, send)
