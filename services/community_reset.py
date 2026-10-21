"""Fail-closed, durable withdrawal retry. Personal reports and ownership are never deleted."""
import json
import threading
import time
_lock=threading.RLock()

def pending(store=None):
    if store is None:
        from services.community_store import CommunityStore
        store=CommunityStore.open()
    try:return json.loads(store.meta('revoke_pending') or 'null')
    except (ValueError,TypeError):return None

def stop_work():
    from services import community_raw_backfill,community_uploader,db_optimize,maintenance_service
    from services.crawl_manager import crawl_manager
    maintenance_service.stop_for_account()
    community_raw_backfill.stop()
    db_optimize.pause_for_account()
    crawl_manager.stop_for_account_binding()
    community_uploader.stop_background(timeout=0)
    try:
        from services import community_schedule
        from core.utils import scheduler as scheduler_module
        community_schedule.pause_upload_jobs(scheduler_module.scheduler)
        from services.community_store import CommunityStore
        paused=[]
        for job in scheduler_module.scheduler.get_jobs():
            if job.func==scheduler_module.run_crawler:
                scheduler_module.scheduler.pause_job(job.id);paused.append(job.id)
        CommunityStore.open().set_meta('paused_crawl_jobs',json.dumps(paused))
    except (ImportError,AttributeError):pass

def begin(grant=None):
    from services import community_auth_service as cas,community_cloud as cloud,community_gate as gate
    from services.community_store import CommunityStore
    service=cas.get_service();current=service.store.load().get('current') or {}
    if not current.get('user_id'):raise cas.CommunityAuthError('invalid_state','철회에 필요한 카카오 로그인이 없습니다.')
    cloud.deny('consent_revoked',sticky=True)
    cloud.observe(service,kind='revoked',source='local_revoke_pending')
    store=CommunityStore.open()
    if pending(store) is None:
        store.set_meta('revoke_pending',json.dumps(dict(grant_id=grant,account=gate.account_fingerprint(current['user_id']),session_connected_at=current.get('connected_at'),attempts=0,next_attempt_at=0)))
    gate.invalidate('revoke_pending');stop_work()

def session_is_fresh(item,current):
    """철회 요청 이후 새로 만든 카카오 세션(connected_at 이 요청 때와 다름)이면 True. 옛 pending(기록 없음)은 False."""
    then=(item or {}).get('session_connected_at');now=(current or {}).get('connected_at')
    return bool(then and now and now!=then)

def attempt(*,force=False):
    from services import community_auth_service as cas,community_gate as gate,community_account_ops as ops
    from services.community_store import CommunityStore
    with _lock:
        store=CommunityStore.open();item=pending(store)
        if not item:return True
        if not force and time.time()<item.get('next_attempt_at',0):return False
        service=cas.get_service();current=service.store.load().get('current') or {}
        if not current.get('user_id') or gate.account_fingerprint(current['user_id'])!=item['account']:return False
        from services.crawl_manager import crawl_manager
        generation=crawl_manager.restore_generation()
        def still_pending():
            latest=pending(store)
            session=service.store.load().get('current') or {}
            return bool(latest and latest.get('account')==item['account'] and
                        session.get('user_id') and gate.account_fingerprint(session['user_id'])==item['account'] and
                        crawl_manager.restore_generation()==generation)
        try:
            grant=item.get('grant_id')
            if not grant:
                status=ops.account_call(lambda c,t:c.status(t))
                if not still_pending():return False
                consent=status.get('consent') or {}
                if consent.get('state') in ('none','revoked'):
                    ops.soft_reset('consent_revoked',keep_session=session_is_fresh(item,service.store.load().get('current')));return True
                grant=consent.get('grant_id')
                if not grant:raise cas.CommunityAuthError('invalid_state','철회할 동의를 확인하는 중입니다.')
                item['grant_id']=grant;store.set_meta('revoke_pending',json.dumps(item))
            result=ops.account_call(lambda c,t:c.consent_revoke(t,grant))
            if not still_pending():return False
            if not (result.get('revoked') is True or result.get('already_revoked') is True or result.get('lineage_active') is False):
                raise cas.CommunityAuthError('invalid_state','철회 확인을 기다리고 있습니다.')
            ops.soft_reset('consent_revoked',keep_session=session_is_fresh(item,service.store.load().get('current')));return True
        except cas.CommunityAuthError as exc:
            if not still_pending():return False
            if exc.code in ("stale_grant","account_stale_grant"):item["grant_id"]=None
            item['attempts']+=1;item['next_attempt_at']=time.time()+min(1800,60*2**min(item['attempts']-1,5))
            store.set_meta('revoke_pending',json.dumps(item));gate.invalidate('revoke_pending')
            return False


def resume_work():
    from services import maintenance_service,db_optimize
    from services.community_store import CommunityStore
    from core.utils import scheduler
    maintenance_service.resume_for_account();db_optimize.resume_for_account()
    store=CommunityStore.open()
    for job in json.loads(store.meta('paused_crawl_jobs') or '[]'):
        try:scheduler.scheduler.resume_job(job)
        except Exception as exc:
            from core.utils.fallback import note_fallback
            note_fallback('community_reset.resume_job',exc)
    with store.transaction() as tx:tx.execute("DELETE FROM meta WHERE key='paused_crawl_jobs'")
