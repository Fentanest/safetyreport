"""Bounded backfill state machine; production keeps per-report state in SQLite.

2026-10-22(규칙 문서 backfill-resume-20261022/CLIENT-RULES.md 1·2·3절, 모바일 lib/community/raw_backfill.dart 와 1:1):
- 오류는 일시적(네트워크·시간 초과·5xx·429·result 없음·잠금·DB 잠김·점검)과 고정적(파서 예외·저장 실패·분류 못 한 예외)으로 나눈다.
  일시적이면 신고 시도 횟수를 올리지 않고 run 을 paused(60→300→900→3600초)로 둔다. 고정적이면 신고별 3회에 보류(hold).
- 보류는 hold_count 에 따라 15분·1시간·6시간·24시간 뒤 자동으로 다시 시도하고(attempts=2), 30일이 지나면 stale 표시만 한다.
- 업로드 확인 retry 3회는 보류(upload_retry). 서버가 재시도 불가로 막은 blocked 만 not_eligible.
- 업로드 일시 중지(점검) 동안은 run 을 paused/maintenance 로 두고, 필수 버전 잠금 중에는 아무것도 하지 않는다.
"""
from __future__ import annotations
import copy
import errno
import json
import socket
import sqlite3
import ssl
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
class BackfillTransient(RuntimeError):
    """일시적 오류(신고 시도 횟수에 넣지 않음). reason 은 진행 보고와 같은 사유 코드, 문자열은 기존 코드('detail_retry')."""
    def __init__(self,reason,message='detail_retry'):
        super().__init__(message);self.reason=reason

# 연속 일시적 오류 횟수(1부터) → run 쉬는 시간. 4회 이상은 1시간.
TRANSIENT_DELAYS=(60,300,900,3600)
# 보류 횟수 hold_count(1부터) → 다음 자동 재시도까지. 4회 이상은 24시간 고정.
HOLD_DELAYS=(900,3600,6*3600,24*3600)
STALE_AFTER=30*24*3600
HOLD_FIELDS=('hold_count','next_attempt_at','first_held_at','stale')
UNSTICK_KEY='raw_backfill_unstick'
UNSTICK_VERSION='2026-10-22.1'
_FIXED_RUNTIME=('parse_retry','detail_save_retry','missing_report','raw_event_not_created')
_NETWORK_ERRNOS=frozenset(getattr(errno,n) for n in ('ENETUNREACH','EHOSTUNREACH','ECONNREFUSED','ECONNRESET','ECONNABORTED',
    'ETIMEDOUT','ENETDOWN','EHOSTDOWN','EPIPE','ENOTCONN','ENETRESET','EAI_AGAIN') if hasattr(errno,n))

def _http_kinds():
    """(timeout 류, network 류) 예외 클래스. requests·urllib3·curl_cffi 가 없으면 빼고 만든다."""
    timeouts,networks=[socket.timeout,TimeoutError],[]
    try:
        import requests
        timeouts.append(requests.Timeout);networks+=[requests.ConnectionError,requests.exceptions.ChunkedEncodingError]
    except ImportError:pass
    try:
        import urllib3.exceptions as u3
        timeouts.append(u3.TimeoutError);networks.append(u3.HTTPError)
    except ImportError:pass
    try:
        from curl_cffi.requests import exceptions as cce
        timeouts.append(cce.Timeout);networks+=[cce.ConnectionError,cce.ChunkedEncodingError,cce.ProxyError]
    except ImportError:pass
    return tuple(timeouts),tuple(networks)
_TIMEOUTS,_NETWORKS=_http_kinds()

def _db_locked(exc):
    text=str(exc).lower()
    if isinstance(exc,sqlite3.OperationalError):return 'locked' in text or 'busy' in text
    try:
        from sqlalchemy.exc import OperationalError
    except ImportError:return False
    return isinstance(exc,OperationalError) and ('database is locked' in text or 'database is busy' in text or 'locked' in text)

def transient_reason(exc):
    """일시적 오류의 사유 코드(규칙 문서 1절). 일시적이 아니면 None."""
    if isinstance(exc,BackfillTransient):return exc.reason
    if isinstance(exc,BackfillPaused):return str(exc)
    if isinstance(exc,RuntimeError) and str(exc)=='detail_retry':return 'server_5xx'
    if isinstance(exc,_TIMEOUTS):return 'timeout'
    if isinstance(exc,_NETWORKS):return 'network'
    try:
        from curl_cffi.curl import CurlError
        if isinstance(exc,CurlError):
            # 응답을 해석하다 난 curl 오류(JSON·HTTP 상태)는 같은 입력이면 같은 결과다 — 그 밖의 curl 오류는 전송 계층.
            name=type(exc).__name__
            if name not in ('InvalidJSONError','HTTPError','InvalidURL','MissingSchema','InvalidSchema'):
                return 'timeout' if 'Timeout' in name or 'timed out' in str(exc).lower() else 'network'
    except ImportError:pass
    if _db_locked(exc):return 'db_locked'
    if isinstance(exc,(ConnectionError,socket.gaierror,ssl.SSLError)):return 'network'
    if isinstance(exc,OSError) and exc.errno in _NETWORK_ERRNOS:return 'network'
    return None

def classify_backfill_error(exc):
    """'transient' | 'fixed'. 분류 못 한 예외는 고정적이다(보류 자동 재시도가 있어 영구 정지는 없다)."""
    if isinstance(exc,RuntimeError) and str(exc) in _FIXED_RUNTIME:return 'fixed'
    return 'transient' if transient_reason(exc) is not None else 'fixed'

def transient_delay(failures):
    return TRANSIENT_DELAYS[min(max(1,int(failures)),len(TRANSIENT_DELAYS))-1]

def hold_item(item,now,error=None):
    """고정적 오류 3회(또는 업로드 확인 retry 3회): 보류로 두고 다음 자동 재시도 시각을 정한다."""
    count=int(item.get('hold_count') or 0)+1
    first=item.get('first_held_at')
    first=now if first is None else first
    item.update(state='hold',hold_count=count,next_attempt_at=int(now+HOLD_DELAYS[min(count,len(HOLD_DELAYS))-1]),
                first_held_at=first,stale=now-first>STALE_AFTER)
    if error:item['error']=error
    return item

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
            if i['state']=='hold':
                i.update(state='pending',attempts=0,event_id=None,hold_count=0,stale=False);i.pop('next_attempt_at',None)
    def release_due_holds(self,run,now):
        count=0
        for i in self.items.values():
            if i['state']=='hold' and (i.get('next_attempt_at') or 0)<=now:i.update(state='pending',attempts=2,event_id=None);count+=1
        return count
    def next_hold_at(self,run):
        due=[i.get('next_attempt_at') or 0 for i in self.items.values() if i['state']=='hold']
        return min(due) if due else None
    def page(self,run,cursor,limit):return [(p,k,copy.deepcopy(v)) for p,(k,v) in enumerate(self.items.items()) if p>=cursor][:limit]
    def queued(self,run,limit):return [(k,copy.deepcopy(v)) for k,v in self.items.items() if v['state']=='queued'][:limit]
    def put(self,run,rid,item):self.items[rid]=copy.deepcopy(item)
    def put_many(self,run,entries):
        for rid,item in entries:self.put(run,rid,item)
    def summary(self,run):return summarize(self.items.values())
    def counts(self,run):return count_states(self.items.values())
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

def count_states(values):
    """진행 보고(규칙 문서 4-1)의 개수: run 항목 수와 상태별 수, 30일 넘은 보류 수."""
    out=dict(total=0,stored=0,queued=0,pending=0,holds=0,stale_holds=0,raw_unavailable=0,not_eligible=0)
    key={'stored':'stored','queued':'queued','pending':'pending','hold':'holds','raw_unavailable':'raw_unavailable','not_eligible':'not_eligible'}
    for item in values:
        out['total']+=1
        if item['state'] in key:out[key[item['state']]]+=1
        out['stale_holds']+=bool(item['state']=='hold' and item.get('stale'))
    return out

class RawBackfill:
    def __init__(self,load,save,backend,*,clock=time.time,items=None):
        self.load,self.save,self.backend,self.clock=load,save,backend,clock
        self.repository=items
        self.changed=0  # 이번 run 에서 상태가 바뀐 항목 수(스레드 루프가 진척 없음을 알아본다)
        self.woke=0  # 이번 run 에서 업로더를 깨운 횟수(시험용)
    def run(self,*,budget=1000,retry_holds=False):
        b=self.backend;scope=b.scope();self.changed=0;self.woke=0
        if not scope or not b.current(scope):return None
        if getattr(b,'update_locked',lambda:False)():return None  # 필수 버전 잠금: marker 를 바꾸지 않고 아무것도 하지 않는다(6절)
        m=self.load();repo=self.repository or MemoryItems()
        if not m or any(m.get(k)!=scope.get(k) for k in RUN_IDENTITY):
            m=dict(contract='v2',**scope,run_id=str(uuid.uuid4()),state='waiting_optimize',phase='listing',listing_complete=False,cursor=0,stats={},holds={},
                   failures=0,transient_failures=0,next_attempt_at=0,run_started_at=int(self.clock()))
            repo.reset(m['run_id'],[])
        elif 'items' in m:
            repo.load_legacy(m['run_id'],m['items'])
            m['listing_complete']=m.get('listing_complete',m.get('phase')!='listing')
        run=m['run_id']
        m.update(scope)  # 식별 외 범위(grant·writer 등)는 갱신만 하고 run 을 바꾸지 않는다
        if retry_holds:
            # '다시 시도' 버튼: 시각과 관계없이 모든 보류를 되돌린다. 현재 단계를 유지한 채 보류·휴지만 푼다.
            # 목록이 끝나지 않았으면 채우기로 건너뛰지 않는다(0건 done 방지).
            repo.retry(run);m.update(failures=0,transient_failures=0,next_attempt_at=0)
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
            m['updated_at']=int(self.clock())
            self.save(copy.deepcopy(m),scope)
        def wake():
            # 중지(점검)·필수 버전 잠금 중에는 깨우지 않는다. 한 번의 run 에서 한 번만.
            if self.woke or getattr(b,'upload_pause',lambda:None)() or getattr(b,'update_locked',lambda:False)():return
            self.woke+=1;b.wake_upload()
        if m['state']=='done' and not retry_holds:return m
        try:
            if m.get('login_fp') is not None:
                # 로그인 거부로 멈춘 run: 계정 설정이 바뀌었거나 새 토큰이 생겼으면(수동 로그인) 곧바로 이어 간다.
                fingerprint=getattr(b,'login_fingerprint',lambda:None)()
                if fingerprint is not None and fingerprint!=m['login_fp']:m.update(next_attempt_at=0,login_fp=None,login_failures=0)
            pause=getattr(b,'upload_pause',lambda:None)()
            if pause:
                # 업로드 일시 중지(5절): 신고 횟수를 세지 않고 run 을 멈춘다. 재개 시각이 없으면 타이머 없이(None) status 를 기다린다.
                wake_at=pause.get('wake_at')
                if (m.get('state'),m.get('reason'),m.get('next_attempt_at'))!=('paused','maintenance',wake_at):
                    m.update(state='paused',reason='maintenance',next_attempt_at=wake_at);publish()
                return m
            if m.get('reason')=='maintenance':m['next_attempt_at']=0  # 중지가 풀렸다(저장이 지워짐) — 재개 시각을 더 기다리지 않는다
            if self.clock()<(m.get('next_attempt_at') or 0):return m
            m.pop('reason',None);m['next_attempt_at']=0
            if not b.optimization_ready():m['state']='waiting_optimize';publish();return m
            if m.get('phase')=='listing' or not m.get('listing_complete'):
                m.update(phase='listing',state='listing');publish()
                try:inventory=b.inventory()
                except (BackfillPaused,BackfillCancelled):raise
                except Exception:raise BackfillPaused('list_failed') from None
                if not inventory.get('list_ok'):raise BackfillPaused('list_incomplete')
                m['login_failures']=0;m['transient_failures']=0
                existing=set(b.local_ids());eligible=set(b.eligible_ids());missing=set(inventory['ids'])-existing
                remote_eligible=set(inventory.get('eligible_ids',[]))
                repo.reset(run,((rid,dict(state='pending',attempts=0,eligible=rid in eligible or rid in remote_eligible,was_missing=rid in missing)) for rid in sorted(eligible|missing)))
                m.update(phase='filling',state='filling',cursor=0,listing_complete=True);publish()
            # 채우기 slice 시작: 다시 시도할 시각이 지난 보류를 pending(attempts=2, 한 번 더 실패하면 다시 보류)으로 되돌린다.
            released=repo.release_due_holds(run,self.clock())
            if released:m.update(phase='filling',cursor=0);self.changed+=released
            if m['phase']=='filling':
                m['state']='filling';queued_now=0
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
                                item['state']='queued';queued_now+=1
                            for key in HOLD_FIELDS:item.pop(key,None)
                            m['failures']=0;m['login_failures']=0;m['transient_failures']=0;m['cursor']=index+1;self.changed+=1
                        except BackfillBlocked as exc:
                            # 삭제했거나 보낼 수 없는 신고는 분모에서 빼고 건너뛴다(보류로 done 을 막지 않는다).
                            item.update(state='not_eligible',eligible=False,error=str(exc));m['cursor']=index+1;self.changed+=1
                        except (BackfillPaused,BackfillCancelled):raise
                        except Exception as exc:
                            if classify_backfill_error(exc)=='transient':
                                # 일시적: 신고 횟수를 올리지 않고 커서도 그대로 둔 채 run 을 쉰다.
                                m['cursor']=index
                                raise BackfillPaused(transient_reason(exc)) from None
                            item['attempts']+=1;m['failures']+=1;item['error']=str(exc) if str(exc) in _FIXED_RUNTIME else type(exc).__name__
                            if item['attempts']>=3:hold_item(item,self.clock());m['cursor']=index+1;self.changed+=1
                            else:m['cursor']=index
                            stage(rid,item)
                            if m['failures']>=5:raise BackfillPaused('consecutive_failures')
                            if item['attempts']<3:break
                        stage(rid,item)
                finally:
                    flush()
                    if queued_now:wake()  # 2-4: 이 slice 에서 새로 queued 된 항목이 있으면 업로더를 한 번 깨운다
                if m['cursor']>=repo.size(run):m.update(phase='uploading',state='uploading')
            if m['phase']=='uploading':
                now=self.clock()
                try:
                    for rid,item in repo.queued(run,budget):
                        if getattr(b,'yield_requested',lambda:False)():break
                        result=b.receipt(rid,item['event_id'])
                        if result=='stored':item['state']='stored'
                        elif result=='blocked':item.update(state='not_eligible',eligible=False,error='upload_rejected')
                        elif result=='rebind':item.update(state='pending',attempts=0);m.update(phase='filling',cursor=0)
                        elif result=='retry':
                            item['attempts']=item.get('attempts',0)+1
                            if item['attempts']>=3:hold_item(item,now,'upload_retry')  # 영구 제외 대신 보류(자동 재시도)
                            else:item['state']='pending';m.update(phase='filling',cursor=0)
                        else:continue  # 아직 ACK 대기 — 상태가 바뀌지 않았으니 쓰지 않는다
                        self.changed+=1;stage(rid,item)
                finally:flush()
                if m['phase']=='uploading' and repo.counts(run)['pending']:
                    # 업로드 확인 단계에 pending 이 남아 있으면(1회 초기화로 되돌린 항목 등) 채우기로 돌아간다(모바일과 같음).
                    m.update(phase='filling',cursor=0)
                stats=repo.summary(run)
                complete=m.get('listing_complete') is True and stats['unfinished']==0
                m['state']='done' if complete else ('holds' if stats['holds'] else m['phase'])
                if m['state']=='holds' and stats['unfinished']==stats['holds']:
                    # 보류만 남았다: 가장 이른 보류 시각에 다시 깬다(그 전에는 할 일이 없다 — 스케줄러 1회 작업이 깨운다).
                    m['next_attempt_at']=repo.next_hold_at(run) or 0
                if not complete:wake()
            publish();return m
        except BackfillPaused as exc:
            reason=str(exc)
            if reason in LOGIN_REASONS:
                # 공식 로그인 실패: 확정 거부는 사용자가 고칠 때까지 멈추고, 그 밖의 일시 실패는 지수 백오프(최대 1시간)
                m['login_failures']=int(m.get('login_failures',0))+1
                if getattr(exc,'permanent',False):
                    delay=PERMANENT_PAUSE;m['login_fp']=getattr(b,'login_fingerprint',lambda:None)()
                else:delay=min(LOGIN_BACKOFF_MAX,300*2**(m['login_failures']-1))
            else:
                # 일시적 오류·목록 실패·연속 고정적 오류: 60→300→900→3600초
                m['transient_failures']=int(m.get('transient_failures',0))+1
                delay=transient_delay(m['transient_failures'])
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

def unstick_once(store,clock=time.time):
    """2-3: 새 버전 첫 실행에 한 번, 이미 멈춘 run 을 푼다(규칙 문서 2절 표). 끝나면 meta raw_backfill_unstick 을 쓴다.
    지금 run 의 항목과 marker 를 한 트랜잭션으로 바꾼다. 이미 했으면 None, 했으면 {'reverted': N, 'state': marker 상태}."""
    if store.meta(UNSTICK_KEY) is not None:return None
    with store.transaction() as tx:
        if tx.execute('SELECT 1 FROM meta WHERE key=?',(UNSTICK_KEY,)).fetchone():return None
        row=tx.execute("SELECT value FROM meta WHERE key='raw_backfill'").fetchone()
        try:marker=json.loads(row[0]) if row else None
        except (TypeError,ValueError):marker=None
        reverted=0
        if isinstance(marker,dict) and marker.get('run_id'):
            run=marker['run_id']
            reverted+=tx.execute("UPDATE raw_backfill_items SET state='pending',attempts=0,event_id=NULL,"
                "detail_json=json_remove(json_set(detail_json,'$.hold_count',0,'$.stale',json('false')),'$.next_attempt_at')"
                " WHERE run_id=? AND state='hold'",(run,)).rowcount
            reverted+=tx.execute("UPDATE raw_backfill_items SET state='pending',attempts=0,eligible=1,event_id=NULL,"
                "detail_json=json_remove(detail_json,'$.error') WHERE run_id=? AND state='not_eligible'"
                " AND json_extract(detail_json,'$.error')='upload_rejected'",(run,)).rowcount
            for item in (marker.get('items') or {}).values():  # 아주 옛 marker(항목을 marker 안에 둔 것)
                if item.get('state')=='hold':
                    item.update(state='pending',attempts=0,event_id=None,hold_count=0,stale=False);item.pop('next_attempt_at',None);reverted+=1
                elif item.get('state')=='not_eligible' and item.get('error')=='upload_rejected':
                    item.update(state='pending',attempts=0,eligible=True,event_id=None);item.pop('error',None);reverted+=1
            state=marker.get('state')
            manual_login=marker.get('reason')=='official_login_required' and marker.get('login_fp') is not None
            if state=='paused' and not manual_login:
                marker.update(next_attempt_at=0,transient_failures=0,failures=0)
            if state in ('done','holds') and reverted:
                marker.update(state='filling',phase='filling',cursor=0,next_attempt_at=0)
            store.set_meta('raw_backfill',json.dumps(marker,ensure_ascii=False),tx)
        store.set_meta(UNSTICK_KEY,UNSTICK_VERSION,tx)
    return dict(reverted=reverted,state=(marker or {}).get('state') if isinstance(marker,dict) else None)

def _blocked_now(store):
    """필수 버전 잠금 중이거나 업로드 일시 중지 중이면 사유 코드."""
    try:
        from services import community_update_lock,community_upload_pause
        if community_update_lock.locked(store):return 'update_required'
        if community_upload_pause.read(store):return 'maintenance'
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback('raw_backfill.blocked_check',exc)
    return None

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
    try:
        from services.community_store import CommunityStore
        store=CommunityStore.open()
        unstick_once(store)  # 새 버전 첫 실행에 한 번(끝난 run 도 풀어야 하므로 아래 _finished 보다 먼저)
        if _blocked_now(store)=='update_required':return  # 필수 버전 잠금 중에는 1회 전송을 하지 않는다
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback('raw_backfill.unstick',exc)
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
    _schedule_wake(status(store))

WAKE_JOB_ID='community-raw-backfill-wake'
def _schedule_wake(marker):
    """보류만 남은 run(holds): 가장 이른 보류 시각에 스케줄러 1회 작업으로 다시 깬다(60초 poll 은 그대로 둔다)."""
    try:
        if not marker or marker.get('state')!='holds':return
        at=marker.get('next_attempt_at') or 0
        if at>time.time():
            from services import community_schedule
            community_schedule.schedule_once(WAKE_JOB_ID,at,start)
    except Exception as exc:
        from core.utils.fallback import note_fallback
        note_fallback('raw_backfill.schedule_wake',exc)
