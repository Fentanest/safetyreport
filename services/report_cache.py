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
from collections import OrderedDict
from pathlib import Path

_lock = threading.RLock()
_entries = OrderedDict()
_probes = {}
_limit = 8


def clear():
    with _lock:
        _entries.clear()
        for probe in _probes.values():
            probe.close()
        _probes.clear()


atexit.register(clear)


def cached(fn):
    @functools.wraps(fn)
    def run(engine, *args, **kwargs):
        path = engine.url.database
        if not path or path == ':memory:':
            return fn(engine, *args, **kwargs)
        import settings.settings as settings
        from services.agency_registry import REGISTRY_ROOT
        def signature(p):
            try:
                s = os.stat(p)
                return s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns
            except OSError:
                return None
        with _lock:
            # A dedicated observer keeps SQLite's connection-local data_version meaningful.
            if path not in _probes:
                import sqlite3
                if len(_probes) >= _limit:
                    _probes.pop(next(iter(_probes))).close()
                _probes[path] = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro', uri=True, check_same_thread=False)
            version = _probes[path].execute('PRAGMA data_version').fetchone()[0]
            key = (fn.__name__, os.path.realpath(path), signature(path), signature(path + '-wal'), version,
                   signature(Path(path).parent / 'community.db'), signature(str(Path(path).parent / 'community.db') + '-wal'),
                   datetime.date.today().isoformat(), signature(REGISTRY_ROOT / 'manifest.json'), bool(settings.exclude_withdraw),
                   bool(settings.use_representative_records), json.dumps([args, kwargs], sort_keys=True, ensure_ascii=False))
            if key in _entries:
                _entries.move_to_end(key)
                return copy.deepcopy(_entries[key])
        result = fn(engine, *args, **kwargs)
        with _lock:
            _entries[key] = copy.deepcopy(result)
            while len(_entries) > _limit:
                _entries.popitem(last=False)
        return result
    return run
