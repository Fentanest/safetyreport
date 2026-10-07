"""One durable catch-up job per server-confirmed grant, no personal DB changes.

Normal full/force collection preserves personal overrides and notes. A grant job
uses the existing reshare API contract; deleted/tombstoned reports stay blocked.
"""
from __future__ import annotations

import os
import threading
import time

from services import community_cloud as cloud

_tick_lock = threading.Lock()


def tick():
    if not _tick_lock.acquire(blocking=False):
        return
    try:
        _tick()
    finally:
        _tick_lock.release()


def _tick():
    from services import community_gate as gate, community_upload_status as uploads, crawl_run_state
    from services.community_store import CommunityStore
    from services.crawl_manager import crawl_manager
    item = cloud.current_job()
    if not item or cloud.remaining() > 0:
        return
    key, grant, job = item
    if job.get('next_attempt_at', 0) > time.time():
        return
    if not gate.require_fresh()['can_enter']:
        return
    store = CommunityStore.open()
    context = store.active_context()
    if not context or context.get('consent_grant_id') != grant:
        return
    if not store.acquire_lease('consent-catch-up', str(os.getpid()), 120):
        return
    try:
        if job.get('phase') == 'reshare':
            # Explicit consent authorizes retrospective sharing for this account.
            # Reissuing uses a stable grant tag, so retries do not duplicate events.
            uploads.request_reshare(consent_grant=grant, send=False)
            cloud.update_job(key, grant, phase='crawl', state='pending')
            job = {**job, 'phase': 'crawl', 'state': 'pending'}
        if job.get('run_id'):
            outcome = crawl_run_state.read(job['run_id']) or {}
            if outcome.get('state') == 'succeeded':
                cloud.update_job(key, grant, state='succeeded')
                from services import community_uploader
                community_uploader.wake()
                return
            # An existing process owns the crawl. Never overlap manual or orphaned work.
            if crawl_manager.is_crawling() or _alive(outcome.get('pid') or job.get('pid')):
                return
            if outcome.get('state') in ('accepted', 'starting') and time.time() - job.get('updated_at', 0) < 300:
                return  # parent restart in the brief spawn/bind window
        elif crawl_manager.is_crawling():
            return
        from services import crawl_control
        def bind(run_id, pid=None):
            cloud.update_job(key, grant, state='running', run_id=run_id, pid=pid)
        crawl_control.start_crawl(crawl_mode='full', header='=== [동의 후 전체 수집] ===',
                                  broadcast_source='consent_catch_up', force_full=True,
                                  on_run_created=bind)
    except Exception:
        # Fixture blocking, another local job, interruption or cloud outage: durable retry.
        cloud.update_job(key, grant, next_attempt_at=time.time() + 300)
    finally:
        store.release_lease('consent-catch-up', str(os.getpid()))


def _alive(pid):
    if not isinstance(pid, int) or pid < 1:
        return False
    if os.name == 'nt':
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
