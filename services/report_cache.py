"""Bounded derived-result cache keyed by SQLite changes and registry snapshot.

No time-only invalidation: DB identity/WAL changes cover external crawler writes,
data_version covers same-process connections, and memory engines are never cached.
"""
import copy
import atexit
import datetime
import functools
import json
import os
import threading
import sys
import weakref
from collections import OrderedDict
from pathlib import Path

_lock = threading.RLock()
_entries = OrderedDict()
_probes = {}
_limit = 8
_byte_limit = 64 * 1024 * 1024
_sizes = {}
_flights = {}
_generation = 0
_probe_identities = {}
_engine_identities = weakref.WeakKeyDictionary()
_missing_identity = object()


def clear():
    global _generation
    with _lock:
        _generation += 1
        _sizes.clear()
        _probe_identities.clear()
        _entries.clear()
        for probe in _probes.values():
            probe.close()
        _probes.clear()


atexit.register(clear)


def _signature(path):
    try:
        value = os.stat(path)
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns
    except OSError:
        return None


def _weight(value, seen=None):
    seen = set() if seen is None else seen
    if id(value) in seen:
        return 0
    seen.add(id(value))
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        return size + sum(_weight(k, seen) + _weight(v, seen) for k, v in value.items())
    if isinstance(value, (list, tuple, set)):
        return size + sum(_weight(v, seen) for v in value)
    if hasattr(value, 'memory_usage'):
        usage = value.memory_usage(deep=True)
        return size + int(usage.sum() if hasattr(usage, 'sum') else usage)
    return size


def _key(fn, path, args, kwargs):
    import settings.settings as settings
    from services.agency_registry import REGISTRY_ROOT
    import sqlite3
    identity = _signature(path)
    if path in _probes and _probe_identities[path] != (identity[:2] if identity else None):
        _probes.pop(path).close()
        _probe_identities.pop(path)
    if path not in _probes:
        if len(_probes) >= _limit:
            previous = next(iter(_probes))
            _probes.pop(previous).close()
            _probe_identities.pop(previous, None)
        _probes[path] = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro',
                                      uri=True, check_same_thread=False)
        _probe_identities[path] = identity[:2] if identity else None
    version = _probes[path].execute('PRAGMA data_version').fetchone()[0]
    community = str(Path(path).parent / 'community.db')
    return (fn.__module__, fn.__qualname__, path, identity, _signature(path + '-wal'), version,
            _signature(community), _signature(community + '-wal'), datetime.date.today().isoformat(),
            _signature(REGISTRY_ROOT / 'manifest.json'), bool(settings.exclude_withdraw),
            bool(settings.use_representative_records),
            json.dumps([args, kwargs], sort_keys=True, ensure_ascii=False), _generation)


class _Flight:
    """같은 키를 계산 중인 요청 하나. 끝나면 결과를 담아 이미 기다리던 요청끼리 나눈다(기술일지 B-06).
    결과가 커서 영구 캐시에 남기지 않을 때도, 대기 요청이 같은 계산을 차례로 반복하지 않는다."""
    __slots__ = ('event', 'ready', 'value')

    def __init__(self):
        self.event = threading.Event()
        self.ready = False
        self.value = None


def cached(fn):
    @functools.wraps(fn)
    def run(engine, *args, **kwargs):
        path = engine.url.database
        if not path or path == ':memory:':
            return fn(engine, *args, **kwargs)
        path = os.path.realpath(path)
        while True:
            with _lock:
                identity = _signature(path)
                file_identity = identity[:2] if identity else None
                if _engine_identities.get(engine, _missing_identity) != file_identity:
                    # probe 갱신만으로는 pool 안의 이전 inode 연결을 교체할 수 없다.
                    # 처음 관찰하거나 파일이 교체되면 idle 연결을 닫는다. 이미 진행
                    # 중인 read transaction은 자신의 일관된 옛 snapshot을 마칠 수 있다.
                    engine.dispose()
                    _engine_identities[engine] = file_identity
                key = _key(fn, path, args, kwargs)
                if key in _entries:
                    _entries.move_to_end(key)
                    cached_value = _entries[key]
                    owner = False
                    flight = None
                else:
                    cached_value = None
                    flight = _flights.get(key)
                    owner = flight is None
                    if owner:
                        flight = _flights[key] = _Flight()
            if flight is None:
                return copy.deepcopy(cached_value)
            if not owner:
                flight.event.wait()
                if flight.ready:
                    return copy.deepcopy(flight.value)
                continue  # 계산 실패·도중 무효화 — 다시 확인한다
            try:
                result = fn(engine, *args, **kwargs)
                owned = copy.deepcopy(result)
                weight = _weight(owned)
                with _lock:
                    # clear/restore/external writes during computation cannot publish an old result.
                    if _key(fn, path, args, kwargs) == key:
                        flight.value, flight.ready = owned, True
                        if weight <= _byte_limit:
                            _entries[key] = owned
                            _sizes[key] = weight
                            while len(_entries) > _limit or sum(_sizes.values()) > _byte_limit:
                                previous, _ = _entries.popitem(last=False)
                                _sizes.pop(previous, None)
                return result
            finally:
                with _lock:
                    _flights.pop(key, None)
                    flight.event.set()
    return run
