"""Restore forwarded metadata using the socket peer, independently of CSRF/navigation.

Run Uvicorn with proxy_headers=False / --no-proxy-headers to preserve that peer.
Empty/auto trusts loopback and private/internal networks; explicit lists replace
that default. Every request snapshots the replaceable process-wide config.
Invalid forwarded headers are ignored individually, with bounded diagnostics.
"""
from __future__ import annotations

import ipaddress
import logging
import re
import threading
from dataclasses import dataclass
from urllib.parse import urlsplit


class ProxyConfigurationError(ValueError):
    pass


AUTO_NETWORKS = tuple(ipaddress.ip_network(value) for value in (
    '127.0.0.0/8', '::1/128', '10.0.0.0/8', '172.16.0.0/12',
    '192.168.0.0/16', '100.64.0.0/10', '169.254.0.0/16', 'fc00::/7', 'fe80::/10',
))


def _items(value) -> list[str]:
    return [item.strip() for item in (value or '').split(',') if item.strip()]


def trusted_proxy_networks(value: str):
    """Strict explicit IP/CIDR check; keywords are handled by the settings validator."""
    result = []
    for item in _items(value):
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise ProxyConfigurationError(f'신뢰할 프록시 IP 항목 {item!r} 을 쓸 수 없습니다. 프록시가 서버에 접속하는 '
                                          'IP 또는 CIDR 을 쉼표로 구분해 적으세요(1·0·호스트 이름 불가).') from exc
        if network.prefixlen == 0:
            raise ProxyConfigurationError(f'신뢰할 프록시 IP 항목 {item!r} 은 모든 주소를 신뢰하므로 쓸 수 없습니다.')
        result.append(network)
    return tuple(result)


def validate_trusted_proxies_setting(value: str, previous: str = '') -> tuple:
    """Reject new invalid entries; unchanged legacy values retain startup warnings."""
    items = _items(value)
    if 'none' in items and any(item != 'none' for item in items):
        raise ProxyConfigurationError('trusted_proxies none 은 다른 항목과 함께 쓸 수 없습니다.')
    if items != _items(previous):
        trusted_proxy_networks(','.join(item for item in items if item not in ('*', 'auto', 'none')))
    return parse_trusted_proxies(value).warnings


def _address(host):
    address = ipaddress.ip_address(host)
    return address.ipv4_mapped or address if isinstance(address, ipaddress.IPv6Address) else address


@dataclass(frozen=True)
class TrustedProxyConfig:
    networks: tuple
    trust_all: bool = False
    implicit_auto: bool = False
    warnings: tuple = ()

    def trusts(self, host) -> bool:
        if self.trust_all:
            return True
        try:
            address = _address(host)
        except ValueError:
            return False
        return any(address in network for network in self.networks)


def parse_trusted_proxies(value: str) -> TrustedProxyConfig:
    """Lenient startup parse; none wins conflicts and invalid entries are ignored."""
    networks, warnings, trust_all = [], [], False
    items = _items(value)
    if 'none' in items:
        if any(item != 'none' for item in items):
            warnings.append('trusted_proxies none 이 다른 항목과 함께 있어 none 을 우선합니다(전달 헤더 무시).')
        return TrustedProxyConfig((), warnings=tuple(warnings))
    for item in items:
        if item == 'auto':
            networks.extend(AUTO_NETWORKS)
            continue
        if item == '*':
            trust_all = True
            warnings.append("trusted_proxies '*': 모든 연결의 X-Forwarded-* 를 신뢰합니다(이전 Uvicorn 동작). "
                            "외부에서 직접 접속할 수 있으면 주소·HTTPS 정보를 위조할 수 있으니 실제 프록시 IP/CIDR 로 바꾸세요.")
            continue
        try:
            networks.extend(trusted_proxy_networks(item))
        except ProxyConfigurationError:
            warnings.append(f"trusted_proxies 항목 {item!r} 은 유효한 제한 IP/CIDR 이 아니라 무시합니다"
                            "(호스트 이름·1·0·/0 은 지원하지 않음).")
    implicit = not networks and not trust_all
    if implicit:
        if items:
            warnings.append('trusted_proxies 에 쓸 수 있는 항목이 없어 기본값(auto: 루프백·사설·내부 대역 신뢰)을 씁니다.')
        networks = list(AUTO_NETWORKS)
    return TrustedProxyConfig(tuple(networks), trust_all, implicit, tuple(warnings))


_config_lock = threading.Lock()
_current_config = parse_trusted_proxies('')


def get_trusted_proxy_config() -> TrustedProxyConfig:
    with _config_lock:
        return _current_config


def set_trusted_proxies(value: str) -> TrustedProxyConfig:
    config = parse_trusted_proxies(value)
    global _current_config
    with _config_lock:
        _current_config = config
    return config


# Never evict: a recorded peer is warned about at most once for the process lifetime.
_WARNING_PEER_LIMIT = 1024
_warned_peers: set[str] = set()
_warning_lock = threading.Lock()


def _warn_peer_once(peer: str, message: str):
    with _warning_lock:
        if peer in _warned_peers or len(_warned_peers) >= _WARNING_PEER_LIMIT:
            return
        _warned_peers.add(peer)
    from core.utils.logger import LoggerFactory
    log = LoggerFactory.logbot or logging.getLogger('core')
    log.warning(f'[proxy] {peer} {message}')


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


def _relative_location(location: str, scope) -> str:
    """Strip same-host absolute redirect authorities, preserving path/query/fragment."""
    try:
        target = urlsplit(location)
        if target.scheme not in ('http', 'https') or not target.netloc:
            return location
        target_origin = origin_tuple(f'{target.scheme}://{target.netloc}')
        hosts = [v.decode('latin1') for k, v in scope['headers'] if k == b'host']
        # An external port remains external; only a scheme's default port is omitted.
        if target_origin and any(
                origin_tuple(f'{target.scheme}://{host}') == target_origin for host in hosts):
            path = target.path or '/'
            # A path beginning // would become a network-path redirect.
            if path.startswith('//'):
                return location
            return path + ('?' + target.query if target.query else '') + ('#' + target.fragment if target.fragment else '')
    except ValueError:
        pass
    return location


class TrustedProxyHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope['type'] not in ('http', 'websocket'):
            return await self.app(scope, receive, send)
        config = get_trusted_proxy_config()
        peer = scope.get('client')
        scope = dict(scope)
        headers = list(scope['headers'])
        scope['safetyreport.connection_peer'] = peer
        scope['safetyreport.original_host'] = tuple(v.decode('latin1') for k, v in headers if k == b'host')
        scope['safetyreport.original_forwarded_host'] = tuple(
            v.decode('latin1') for k, v in headers if k == b'x-forwarded-host')
        trusted = bool(peer and config.trusts(peer[0]))
        if not trusted:
            if peer and any(k.startswith(b'x-forwarded-') for k, _ in headers):
                _warn_peer_once(peer[0], '가 X-Forwarded-* 를 보냈지만 trusted_proxies 에 없어 무시합니다. '
                                '이 주소가 리버스 프록시라면 trusted_proxies 에 추가하세요.')
        else:
            invalid = []
            forwarded = {}
            for key in (b'x-forwarded-proto', b'x-forwarded-host', b'x-forwarded-for'):
                values = [v.decode('latin1').strip() for k, v in headers if k == key]
                if key != b'x-forwarded-for' and (len(values) > 1 or any(',' in v or not v for v in values)):
                    invalid.append(key.decode('ascii'))
                    values = []
                forwarded[key] = ','.join(values)
            proto = forwarded[b'x-forwarded-proto']
            if proto and proto not in ('http', 'https'):
                invalid.append('x-forwarded-proto')
                proto = ''
            if proto:
                scope['scheme'] = proto.replace('http', 'ws') if scope['type'] == 'websocket' else proto
            host = forwarded[b'x-forwarded-host']
            parsed_host = origin_tuple(scope['scheme'].replace('ws', 'http') + '://' + host) if host else None
            if host and (parsed_host is None or '/' in host or '?' in host or '#' in host):
                invalid.append('x-forwarded-host')
                host = ''
            if host:
                scope['headers'] = [(k, v) for k, v in headers if k != b'host'] + [(b'host', host.encode('latin1'))]
            chain = forwarded[b'x-forwarded-for']
            if chain or any(k == b'x-forwarded-for' for k, _ in headers):
                try:
                    hosts = [str(_address(part.strip())) for part in chain.split(',')]
                except ValueError:
                    invalid.append('x-forwarded-for')
                else:
                    # Walk from socket peer towards client, stopping at the trust boundary.
                    for part in reversed(hosts):
                        scope['client'] = (part, 0)
                        if not config.trusts(part):
                            break
            if invalid:
                _warn_peer_once(peer[0], '의 잘못된 전달 헤더를 무시합니다: ' + ', '.join(invalid))

        async def send_relative(message):
            if message['type'] == 'http.response.start' and 300 <= message['status'] < 400:
                message = dict(message)
                message['headers'] = [
                    (key, _relative_location(value.decode('latin1'), scope).encode('latin1'))
                    if key.lower() == b'location' else (key, value)
                    for key, value in message.get('headers', [])
                ]
            await send(message)

        return await self.app(scope, receive, send_relative)
