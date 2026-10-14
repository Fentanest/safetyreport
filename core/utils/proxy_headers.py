"""Restore external URLs once, before sessions/auth/CSRF, using the socket peer.

Run Uvicorn with proxy_headers=False / --no-proxy-headers. A scope whose client
has already been rewritten cannot establish the original connection's trust.
"""
from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from starlette.responses import PlainTextResponse


class ProxyConfigurationError(ValueError):
    pass


def trusted_proxy_networks(value: str):
    """Empty means none. Only explicit IPs/CIDRs; never boolean or wildcard."""
    if not value.strip():
        return ()
    result = []
    for item in value.split(','):
        try:
            network = ipaddress.ip_network(item.strip(), strict=False)
        except ValueError as exc:
            raise ProxyConfigurationError('trusted_proxies must contain comma-separated proxy IPs/CIDRs; '
                             'booleans, hostnames and wildcards are not supported') from exc
        if network.prefixlen == 0:
            raise ProxyConfigurationError('trusted_proxies cannot trust every address')
        result.append(network)
    return tuple(result)


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
        elif not re.fullmatch(r'[a-z0-9.-]+', host):
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
        self.networks = trusted_proxy_networks(trusted_proxies)

    def _trusted(self, host):
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
