"""Bounded backfill state machine; production keeps per-report state in SQLite."""
from __future__ import annotations
import copy
import json
import threading
import time
import uuid

class BackfillPaused(RuntimeError):
    permanent=False  # True: 공식 로그인 거부가 확정 — 사용자가 설정을 저장하거나 수동 로그인할 때까지 다시 시도하지 않는다
LOGIN_REASONS=('official_login_required','official_login_retry')
LOGIN_BACKOFF_MAX=3600
PERMANENT_PAUSE=10*365*24*3600
class BackfillCancelled(RuntimeError): pass
class BackfillBlocked(RuntimeError): pass

# 다시 도는 조건은 로컬 데이터셋이 바뀔 때(복원·교체)와 소프트 리셋 뒤 재동의(리셋이 marker 를 지운다)뿐이다.
# grant 갱신(정책 개정 재동의)·파서 세대·writer 재결합·연결 변경은 이미 저장된 원본을 다시 보내지 않는다(서버가 재파싱한다).
RUN_IDENTITY=('local_dataset_id',)
FLUSH_EVERY=100

class MemoryItems:
    """Small, deterministic offline test adapter. Runtime uses SqliteItems."""
    def __init__(self):self.items={}
    def load_legacy(self,run,items):self.items=copy.deepcopy(items)
    def reset(self,run,items):self.items=dict(items)
    def retry(self,run):
        for i in self.items.values():
            if i['state']=='hold':i.update(state='pending',attempts=0,event_id=None)
    def page(self,run,cursor,limit):return [(p,k,copy.deepcopy(v)) for p,(k,v) in enumerate(self.items.items()) if p>=cursor][:limit]
    def queued(self,run,limit):return [(k,copy.deepcopy(v)) for k,v in self.items.items() if v['state']=='queued'][:limit]
    def put(self,run,rid,item):self.items[rid]=copy.deepcopy(item)
    def put_many(self,run,entries):
        for rid,item in entries:self.put(run,rid,item)
    def summary(self,run):return summarize(self.items.values())
    def holds(self,run):return {k:dict(reason=i.get('error','upload_rejected'),attempts=i['attempts']) for k,i in self.items.items() if i['state']=='hold'}
    def size(self,run):return len(self.items)


def summarize(values):
    stats=dict(total=0,raw_unavailable=0,available=0,sent=0,filled=0,unfilled=0,missing_reports=0,holds=0,unfinished=0)
    for item in values:
        eligible=item.get('eligible') and item['state']!='not_eligible'
        stats['total']+=bool(eligible)
        stats['raw_unavailable']+=bool(eligible and item['state']=='raw_unavailable')
        stats['sent']+=bool(eligible and item['state']=='stored')
        stats['filled']+=item.get('filled',0);stats['unfilled']+=item.get('unfilled',0)
        stats['missing_reports']+=bool(item.get('was_missing'));stats['holds']+=item['state']=='hold'
        stats['unfinished']+=item['state'] not in ('stored','raw_unavailable','not_eligible')
    stats['available']=stats['total']-stats['raw_unavailable']
    return stats

class RawBackfill:
    def __init__(self,load,save,backend,*,clock=time.time,items=None):
        self.load,self.save,self.backend,self.clock=load,save,backend,clock
        self.repository=items
        self.changed=0  # 이번 run 에서 상태가 바뀐 항목 수(스레드 루프가 진척 없음을 알아본다)
    def run(self,*,budget=1000,retry_holds=False):
        b=self.backend;scope=b.scope();self.changed=0
        if not scope or not b.current(scope):return None
        m=self.load();repo=self.repository or MemoryItems()
        if not m or any(m.get(k)!=scope.get(k) for k in RUN_IDENTITY):
            m=dict(contract='v2',**scope,run_id=str(uuid.uuid4()),state='waiting_optimize',phase='listing',listing_complete=False,cursor=0,stats={},holds={},failures=0,next_attempt_at=0)
            repo.reset(m['run_id'],[])
        elif 'items' in m:
            repo.load_legacy(m['run_id'],m['items'])
            m['listing_complete']=m.get('listing_complete',m.get('phase')!='listing')
        run=m['run_id']
        m.update(scope)  # 식별 외 범위(grant·writer 등)는 갱신만 하고 run 을 바꾸지 않는다
        if retry_holds:
            # 현재 단계를 유지한 채 보류·휴지만 푼다. 목록이 끝나지 않았으면 채우기로 건너뛰지 않는다(0건 done 방지).
            repo.retry(run);m.update(failures=0,next_attempt_at=0)
            if m.get('phase')!='listing' and m.get('listing_complete'):
                m.update(phase='filling',cursor=0)
        dirty={}
        def flush():
            if dirty:
                entries=list(dirty.items());dirty.clear();repo.put_many(run,entries)
        def stage(rid,item):
            dirty[rid]=item
            if len(dirty)>=FLUSH_EVERY:flush()
        def publish():
            if not b.current(scope):raise BackfillCancelled()
            flush()
            stats=repo.summary(run);m['stats']={k:v for k,v in stats.items() if k!='unfinished'}
            # 운영 marker 는 요약만 담는다(보류 상세는 항목 표). 건별 상태를 marker 에 넣으면 O(N) 쓰기가 항목마다 반복된다.
            m['holds']=repo.holds(run) if self.repository is None else stats['holds']
            if self.repository is None:m['items']=copy.deepcopy(repo.items)
            else:m.pop('items',None)
            if m['state']=='done' and not (m.get('listing_complete') is True and stats['unfinished']==0):
                m['state']='listing' if not m.get('listing_complete') else 'filling'  # 불변식: 목록 미완료·미처리 항목이 있으면 done 금지
            self.save(copy.deepcopy(m),scope)
        if m['state']=='done' and not retry_holds:return m
        try:
            if m.get('login_fp') is not None:
                # 로그인 거부로 멈춘 run: 계정 설정이 바뀌었거나 새 토큰이 생겼으면(수동 로그인) 곧바로 이어 간다.
                fingerprint=getattr(b,'login_fingerprint',lambda:None)()
                if fingerprint is not None and fingerprint!=m['login_fp']:m.update(next_attempt_at=0,login_fp=None,login_failures=0)
            if self.clock()<m.get('next_attempt_at',0):return m
            m.pop('reason',None);m['next_attempt_at']=0
            if not b.optimization_ready():m['state']='waiting_optimize';publish();return m
            if m.get('phase')=='listing' or not m.get('listing_complete'):
                m.update(phase='listing',state='listing');publish()
                try:inventory=b.inventory()
                except (BackfillPaused,BackfillCancelled):raise
                except Exception:raise BackfillPaused('list_failed') from None
                if not inventory.get('list_ok'):raise BackfillPaused('list_incomplete')
                m['login_failures']=0
                existing=set(b.local_ids());eligible=set(b.eligible_ids());missing=set(inventory['ids'])-existing
                remote_eligible=set(inventory.get('eligible_ids',[]))
                repo.reset(run,((rid,dict(state='pending',attempts=0,eligible=rid in eligible or rid in remote_eligible,was_missing=rid in missing)) for rid in sorted(eligible|missing)))
                m.update(phase='filling',state='filling',cursor=0,listing_complete=True);publish()
            if m['phase']=='filling':
                m['state']='filling'
                try:
                    for index,rid,item in repo.page(run,int(m['cursor']),budget):
                        if getattr(b,'yield_requested',lambda:False)():break
                        if item['state'] in ('stored','raw_unavailable','not_eligible','hold','queued'):
                            m['cursor']=index+1;continue
                        if not b.current(scope):raise BackfillCancelled()
                        try:
                            prepared=b.prepare(rid)
                            if not b.current(scope):raise BackfillCancelled()
                            item.update(prepared)
                            if not item.get('eligible'):item['state']='not_eligible'
                            elif item.get('raw_unavailable'):item['state']='raw_unavailable'
                            else:
                                item['event_id']=b.enqueue(rid,run)
                                if not item['event_id']:raise RuntimeError('raw_event_not_created')
                                item['state']='queued'
                            m['failures']=0;m['login_failures']=0;m['cursor']=index+1;self.changed+=1
                        except BackfillBlocked as exc:
                            # 삭제했거나 보낼 수 없는 신고는 분모에서 빼고 건너뛴다(보류로 done 을 막지 않는다).
                            item.update(state='not_eligible',eligible=False,error=str(exc));m['cursor']=index+1;self.changed+=1
                        except (BackfillPaused,BackfillCancelled):raise
                        except Exception as exc:
                            item['attempts']+=1;m['failures']+=1;item['error']=type(exc).__name__
                            if item['attempts']>=3:item['state']='hold';m['cursor']=index+1;self.changed+=1
                            else:m['cursor']=index
                            stage(rid,item)
                            if m['failures']>=5:raise BackfillPaused('consecutive_failures')
                            if item['attempts']<3:break
                        stage(rid,item)
                finally:flush()
                if m['cursor']>=repo.size(run):m.update(phase='uploading',state='uploading')
            if m['phase']=='uploading':
                try:
                    for rid,item in repo.queued(run,budget):
                        if getattr(b,'yield_requested',lambda:False)():break
                        result=b.receipt(rid,item['event_id'])
                        if result=='stored':item['state']='stored'
                        elif result=='blocked':item.update(state='not_eligible',eligible=False,error='upload_rejected')
                        elif result in ('retry','rebind'):
                            item.update(state='pending',attempts=0 if result=='rebind' else item.get('attempts',0)+1)
                            if item['attempts']>=3:item.update(state='not_eligible',eligible=False,error='upload_rejected')
                            else:m.update(phase='filling',cursor=0)
                        else:continue  # 아직 ACK 대기 — 상태가 바뀌지 않았으니 쓰지 않는다
                        self.changed+=1;stage(rid,item)
                finally:flush()
                stats=repo.summary(run)
                complete=m.get('listing_complete') is True and stats['unfinished']==0
                m['state']='done' if complete else ('holds' if stats['holds'] else m['phase'])
                if not complete:b.wake_upload()
            publish();return m
        except BackfillPaused as exc:
            reason=str(exc);delay=300
            if reason in LOGIN_REASONS:
                # 공식 로그인 실패: 확정 거부는 사용자가 고칠 때까지 멈추고, 그 밖의 일시 실패는 지수 백오프(최대 1시간)
                m['login_failures']=int(m.get('login_failures',0))+1
                if getattr(exc,'permanent',False):
                    delay=PERMANENT_PAUSE;m['login_fp']=getattr(b,'login_fingerprint',lambda:None)()
                else:delay=min(LOGIN_BACKOFF_MAX,300*2**(m['login_failures']-1))
            m.update(state='paused',reason=reason,next_attempt_at=self.clock()+delay);publish();return m
        except BackfillCancelled:return None

_lock=threading.Lock()
_thread=None
_stop=threading.Event()

def status(store=None):
    if store is None:
        from services.community_store import CommunityStore
        store=CommunityStore.open()
    try:return json.loads(store.meta('raw_backfill') or 'null')
    except (TypeError,ValueError):return None

def retry():
    from services.community_store import CommunityStore
    store=CommunityStore.open();store.set_meta('raw_backfill_retry','true');start()

def stop():_stop.set()

def _finished():
    """marker 가 이 로컬 데이터셋에서 done 이고 다시 시도 요청도 없으면 True."""
    try:
        from services.community_store import CommunityStore
        store=CommunityStore.open()
        marker=status(store)
        return bool(marker and marker.get('state')=='done' and marker.get('local_dataset_id')==store.local_dataset_id()
                    and store.meta('raw_backfill_retry') is None)
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback('raw_backfill.finished_check',exc)
        return False

def start():
    global _thread
    from core.utils.runtime_mode import is_fixture_mode
    if is_fixture_mode():return
    try:  # v2 가 켜져 있지 않으면(대부분의 기존 사용자) 스레드·lease 를 만들지 않는다 — 60초 poll 마다 낭비하지 않게(최종 검수 10)
        from services.community_store import CommunityStore
        from services.community_raw_ingest import v2_ready,ingest_status
        if not v2_ready(ingest_status(CommunityStore.open())):return
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback('raw_backfill.start_check',exc);return
    if _finished():return  # 끝난 run 은 60초 poll 마다 스레드를 만들지 않는다
    with _lock:
        if _thread is not None and _thread.is_alive():return
        _stop.clear();_thread=threading.Thread(target=_run_runtime,name='community-raw-backfill',daemon=True);_thread.start()

def _run_runtime():
    from services.community_raw_backfill_runtime import RuntimeBackend
    from services.community_backfill_store import SqliteItems
    from services.community_store import CommunityStore
    from core.utils.fallback import note_fallback
    store=CommunityStore.open();owner='raw-backfill:'+str(uuid.uuid4())
    if not store.acquire_lease('raw_backfill',owner,120):return
    done=threading.Event()
    def heartbeat():
        while not done.wait(20):
            try:
                if store.renew_lease('raw_backfill',owner,120):continue
            except Exception as exc:note_fallback('raw_backfill.lease',exc)
            _stop.set();return
    threading.Thread(target=heartbeat,name='raw-backfill-lease',daemon=True).start()
    backend=None
    try:
        backend=RuntimeBackend(store,_stop)
        def save(marker,scope):
            with store.transaction() as tx:
                if not backend.current(scope):raise BackfillCancelled()
                store.set_meta('raw_backfill',json.dumps(marker,ensure_ascii=False),tx)
        retry_holds=store.meta('raw_backfill_retry')=='true'
        with store.transaction() as tx:tx.execute("DELETE FROM meta WHERE key='raw_backfill_retry'")
        runner=RawBackfill(lambda:status(store),save,backend,items=SqliteItems(store,backend.check))
        # Each pass is short; releasing the official-work reservation lets crawls/restores run.
        end=time.monotonic()+45
        while not _stop.is_set():
            result=runner.run(budget=1000,retry_holds=retry_holds);retry_holds=False
            backend.release_work()
            if result is None and not backend.yielded:break
            if result and result['state'] in ('done','paused','holds','waiting_optimize'):break
            # 업로더가 ACK 를 줄 때까지 기다리는 중이면(진척 없음) 이 pass 를 끝낸다 — 다음 poll 이 이어 받는다.
            if result and result['state']=='uploading' and not runner.changed:break
            if time.monotonic()>=end:break
            # 짧게 일하고 놓는다: 예약 크롤·DB 복원이 이 사이에 끼어들 수 있다.
            if _stop.wait(1.0):break
    except (BackfillCancelled,BackfillPaused):pass
    except Exception as exc:note_fallback('raw_backfill.runtime',exc)
    finally:
        if backend is not None:backend.close()
        done.set();store.release_lease('raw_backfill',owner)
