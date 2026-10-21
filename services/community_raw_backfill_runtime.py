"""PC adapter: reuses official list/detail parsing and the ordinary personal save path."""
import json
import time
from datetime import datetime
from dateutil.relativedelta import relativedelta
from sqlalchemy import select,update
from core.database import models
from services.community_raw_backfill import BackfillPaused,BackfillCancelled,BackfillBlocked
from services import community_raw_ingest as raw

# 목록(title_pipeline.TITLE_COLUMNS)에서 오는 신고 열. 감시목록은 로컬 값이라 목록으로 덮지 않는다.
_TITLE_KEYS = ("상태", "신고번호", "신고명", "신고일", "만족도조사여부")


def _list_fields(row):
    """목록 DataFrame 한 행 → JSON 으로 보관할 수 있는 신고 열 값(NaN·numpy 값 정리)."""
    out = {}
    for key in ("ID",) + _TITLE_KEYS:
        value = row.get(key)
        if hasattr(value, "item"):
            value = value.item()
        if isinstance(value, float) and value != value:
            value = None
        out[key] = value
    return out


class RuntimeBackend:
    def __init__(self,store,stop):
        from core.database.engine import get_engine
        from services.crawl_manager import crawl_manager
        self.store,self.stop,self.engine=store,stop,get_engine()
        self.manager=crawl_manager;self.generation=crawl_manager.restore_generation()
        self.token=None;self.session=None;self._scope=None;self.slice_until=None;self.yielded=False;self.list_rows={};self._list_checked=False
    def scope(self):
        self.slice_until=None;self.yielded=False
        from services.community_capture import PARSER_VERSION
        ctx=self.store.active_context(); status=raw.ingest_status(self.store)
        if not ctx or not raw.v2_ready(status) or (status.get('consent') or {}).get('grant_id')!=ctx.get('consent_grant_id'):return None
        raw.selected(self.store,status,ctx)
        self._scope={k:ctx[k] for k in ('contributor_fingerprint','dataset_key','consent_grant_id')}
        self._scope.update(local_dataset_id=self.store.local_dataset_id(),parser_target=PARSER_VERSION,connection_id=ctx['connection_id'],writer_epoch=ctx['writer_epoch'])
        return self._scope
    def current(self,scope):
        if self.stop.is_set() or self.manager.restore_generation()!=self.generation:return False
        ctx=self.store.active_context()
        return bool(ctx and self.store.local_dataset_id()==scope['local_dataset_id'] and all(ctx.get(k)==scope[k] for k in ('contributor_fingerprint','dataset_key','consent_grant_id','connection_id','writer_epoch')))
    def yield_requested(self):
        return self.slice_until is not None and time.monotonic()>=self.slice_until
    def check(self):
        if not self._scope or not self.current(self._scope):raise BackfillCancelled()
    def optimization_ready(self):
        from services import db_optimize,maintenance_service
        if maintenance_service.is_running():return False
        if db_optimize.needs_run(self.engine):
            db_optimize.maybe_start(self.engine);return False
        if db_optimize.is_running() or self.manager.is_crawling():return False
        try:self.token=self.manager.reserve_rating(self.generation)
        except RuntimeError:return False
        self.slice_until=time.monotonic()+3
        self.check();return True
    def connect(self):
        self.check()
        if self.session is None:
            from core.crawler import direct_login
            try:self.session=direct_login.make_authorized_session()[0]
            except Exception:raise BackfillPaused('official_login_required') from None
        return self.session
    def pause(self):
        if self.stop.wait(0.5):raise BackfillCancelled()
        self.check()
    def inventory(self):
        from core.crawler.crawltitle_api import crawl_titles
        progress={}
        frames,_=crawl_titles(progress=progress, session_override=self.connect(), stopped=lambda:not self.current(self._scope))
        self.check()
        rows=[r for frame in frames for r in frame.to_dict('records')]
        self.list_rows={str(r['ID']):_list_fields(r) for r in rows}
        # 목록 값은 실행이 끊겨도(3초 slice·재기동) 쓰도록 보관한다(모바일 raw_backfill_supplements kind='list' 와 같음).
        with self.store.transaction() as tx:
            self.check()
            for rid,fields in self.list_rows.items():
                tx.execute('INSERT OR REPLACE INTO raw_backfill_supplements VALUES (?,?,?,?)',(self._scope['local_dataset_id'],rid,'list',json.dumps(fields,ensure_ascii=False,default=str)))
        self._repair_title_fields()
        return dict(list_ok=progress.get('list_ok') is True,ids=[str(r['ID']) for r in rows],eligible_ids=[str(r['ID']) for r in rows if r.get('상태') in ('답변완료','수용','일부수용','불수용','기타')])
    def _repair_title_fields(self):
        """목록 값 없이 저장된 개인 신고(이전 실행·옛 코드)를 방금 받은 목록 값으로 한 번에 채운다. 비어 있는 열만 채운다."""
        from core.storage import reports_repo
        cols=[models.report_table.c.ID]+[models.report_table.c[k] for k in _TITLE_KEYS]
        with self.engine.connect() as c:
            rows=c.execute(select(*cols)).mappings().all()
        fixes={}
        for r in rows:
            listed=self.list_rows.get(str(r['ID'])) or {}
            missing={k:listed[k] for k in _TITLE_KEYS if r[k] is None and listed.get(k) is not None}
            if missing:fixes[str(r['ID'])]=missing
        ids=list(fixes)
        for start in range(0,len(ids),200):
            chunk=ids[start:start+200]
            with self.engine.begin() as c:
                self.check()
                for rid in chunk:c.execute(update(models.report_table).where(models.report_table.c.ID==rid).values(**fixes[rid]))
                reports_repo.refresh_effective_rows(c,chunk);self.check()
        return len(ids)
    def _ensure_list(self):
        """목록 값이 메모리에도 보관 표에도 없으면(옛 코드로 목록 단계를 마친 run 등) 목록을 한 번 다시 받는다."""
        if self.list_rows or self._list_checked:return
        self._list_checked=True
        kept=self.store.connect().execute('SELECT 1 FROM raw_backfill_supplements WHERE local_dataset_id=? AND kind=? LIMIT 1',(self._scope['local_dataset_id'],'list')).fetchone()
        if kept is not None:return
        try:self.inventory()
        except BackfillCancelled:raise
        except Exception as exc:
            # 목록을 못 받아도(로그인·네트워크) 원본 처리는 막지 않는다. 빈 목록 값은 다음 run 의 목록 단계가 채운다.
            from core.utils.fallback import note_fallback
            note_fallback('raw_backfill.ensure_list',exc)
    def _list_row(self,rid):
        live=self.list_rows.get(rid)
        if live:return dict(live)
        kept=self.store.connect().execute('SELECT status FROM raw_backfill_supplements WHERE local_dataset_id=? AND report_id=? AND kind=?',(self._scope['local_dataset_id'],rid,'list')).fetchone()
        if kept:
            try:return dict(json.loads(kept[0]))
            except (TypeError,ValueError):return {}
        return {}
    def local_ids(self):
        with self.engine.connect() as c:return [str(x) for x in c.execute(select(models.report_table.c.ID)).scalars()]
    def eligible_ids(self):
        with self.engine.connect() as c:return [str(x) for x in c.execute(select(models.report_table.c.ID).where(models.report_table.c['처리상태'].in_(['수용','일부수용','불수용','답변완료','기타']))).scalars()]
    def row(self,rid):
        with self.engine.connect() as c:
            row=c.execute(select(models.report_table).where(models.report_table.c.ID==rid)).mappings().first()
        return dict(row) if row else None
    def prepare(self,rid):
        from services import db_optimize,community_capture as cap
        from core.storage import reports_repo
        from core.utils import raw_detail
        from services import satisfaction_fetcher,photo_capture_time
        self.check();row=self.row(rid)
        if row is None or any(row.get(k) is None for k in _TITLE_KEYS):self._ensure_list()
        deleted=self.store.connect().execute("SELECT 1 FROM source_journal WHERE source_report_id=? AND dataset_key IS ? AND contributor_fingerprint IS ? AND blocked_reason IN ('deleted_by_user','deleted','blocked:deleted') LIMIT 1",(rid,self._scope['dataset_key'],self._scope['contributor_fingerprint'])).fetchone()
        if deleted:raise BackfillBlocked('deleted')
        snapshot=raw.load_current_snapshot(rid,engine=self.engine)
        filled=unfilled=0
        photo_unavailable=None
        if row is None or snapshot is None:
            envelope,outcome,_=db_optimize._fetch_envelope(self.connect(),rid,attempts=1,interval=0)
            self.pause()
            if outcome=='auth':raise BackfillPaused('official_login_required')
            if outcome=='permanent':return dict(raw_unavailable=True,filled=0,unfilled=1)
            if outcome!='ok':raise RuntimeError('detail_retry')
            details=db_optimize._parse_result(rid,envelope['result'])
            if details is None:raise RuntimeError('parse_retry')
            rec=db_optimize._build_rec(rid,details)
            rec.title_fields={k:row.get(k) for k in models.report_table.c.keys() if row is not None and k in row} if row else self._list_row(rid)
            canonical=raw_detail.canonical_bytes(envelope)
            rec.raw_canonical=canonical;rec.raw_sha256=raw_detail.sha256_hex(canonical);rec.raw_size=len(canonical);rec.raw_source='raw_backfill'
            self.check()
            result=reports_repo.save_crawled(self.engine,[rec],refresh_duplicates=False,admission=self.check, capture_community=False)
            if result.failed or not result.saved:raise RuntimeError('detail_save_retry')
            filled+=1;row=self.row(rid)
        if row is None:raise RuntimeError('missing_report')
        listed=self._list_row(rid)
        missing={k:v for k,v in listed.items() if v is not None and row.get(k) is None and k in _TITLE_KEYS}
        if missing:
            # 이전 실행이 목록 값 없이 저장한 신고를 보관한 목록 값으로 채운다.
            with self.engine.begin() as c:
                self.check();c.execute(update(models.report_table).where(models.report_table.c.ID==rid).values(**missing))
                reports_repo.refresh_effective_rows(c,[rid]);self.check()
            row=self.row(rid)
        if row.get('처리상태') not in ('수용','일부수용','불수용','답변완료','기타'):return dict(eligible=False,filled=filled,unfilled=0)
        prior=self.store.connect().execute('SELECT status FROM raw_backfill_supplements WHERE local_dataset_id=? AND report_id=? AND kind=?',(self._scope['local_dataset_id'],rid,'rating')).fetchone()
        if prior is None and row.get('만족도조사여부')=='참여 완료' and (row.get('별점') is None or not row.get('별점사유')):
            outcome='success'
            try:
                result=satisfaction_fetcher.fetch_score_via_api(self.connect(),row.get('신고번호') or '')
                self.pause()
                if not result.confirmed:raise RuntimeError('rating_retry')
                values={'별점':result.score,'별점사유':result.cause}
                if result.score is None:values['만족도조사여부']='참여 가능'
                with self.engine.begin() as c:
                    self.check();c.execute(update(models.report_table).where(models.report_table.c.ID==rid).values(**values))
                    reports_repo.refresh_effective_rows(c,[rid]);self.check()
                filled+=1;row=self.row(rid)
            except BackfillCancelled:raise
            except Exception:
                outcome='unavailable';unfilled+=1
            with self.store.transaction() as tx:
                self.check();tx.execute('INSERT OR REPLACE INTO raw_backfill_supplements VALUES (?,?,?,?)',(self._scope['local_dataset_id'],rid,'rating',outcome))
        prior=self.store.connect().execute('SELECT status FROM raw_backfill_supplements WHERE local_dataset_id=? AND report_id=? AND kind=?',(self._scope['local_dataset_id'],rid,'photo')).fetchone()
        if prior is None and photo_capture_time.photo_urls(row.get('첨부사진')) and row.get('사진_촬영수') is None:
            outcome='success'
            try:
                day=cap._parse_day(row.get('신고일'))
                expired=day is not None and day<(datetime.now()-relativedelta(months=6)).strftime('%Y-%m-%d')
                if expired:unfilled+=1;photo_unavailable='expired'
                else:
                    def fetch(url):
                        self.check();answer=photo_capture_time.fetch_capture_time(url);self.pause();return answer
                    values=photo_capture_time.collect(row['첨부사진'],fetch=fetch)
                    if values is None:raise RuntimeError('photo_retry')
                    with self.engine.begin() as c:
                        self.check();c.execute(update(models.report_table).where(models.report_table.c.ID==rid,models.report_table.c['첨부사진']==row['첨부사진']).values(**values))
                        reports_repo.refresh_effective_rows(c,[rid]);self.check()
                    filled+=int(values['사진_촬영수']>0);unfilled+=int(values['사진_촬영수']==0)
                    if values['사진_촬영수']==0:photo_unavailable='no_exif'
            except BackfillCancelled:raise
            except Exception:
                outcome='unavailable';unfilled+=1
            with self.store.transaction() as tx:
                self.check();tx.execute('INSERT OR REPLACE INTO raw_backfill_supplements VALUES (?,?,?,?)',(self._scope['local_dataset_id'],rid,'photo',outcome))
        self.check()
        return dict(eligible=True,raw_unavailable=False,filled=filled,unfilled=unfilled,photo_unavailable=photo_unavailable)
    def enqueue(self,rid,run):
        from services import community_capture as cap
        self.check();row=self.row(rid)
        if row is None:raise RuntimeError('missing_report')
        listed=self._list_row(rid)
        missing={k:v for k,v in listed.items() if v is not None and row.get(k) is None and k in _TITLE_KEYS}
        if missing:
            # 이전 실행이 목록 값 없이 저장한 신고를 보관한 목록 값으로 채운다.
            with self.engine.begin() as c:
                self.check();c.execute(update(models.report_table).where(models.report_table.c.ID==rid).values(**missing))
                reports_repo.refresh_effective_rows(c,[rid]);self.check()
            row=self.row(rid)
        if cap.agency_code_too_long(row.get('처리기관코드')):raise BackfillBlocked('agency_code_too_long')
        prior=self.store.connect().execute("SELECT event_id,ack_status FROM source_journal WHERE source_report_id=? AND capture_trigger=? AND consent_grant_id=? AND local_dataset_id=? AND connection_id=? AND writer_epoch=? ORDER BY source_revision DESC LIMIT 1",(rid,'raw_backfill:'+run,self._scope['consent_grant_id'],self._scope['local_dataset_id'],self._scope['connection_id'],self._scope['writer_epoch'])).fetchone()
        if prior:
            box=self.store.connect().execute('SELECT state FROM outbox WHERE event_id=?',(prior['event_id'],)).fetchone()
            if self.store.meta('raw_receipt:'+prior['event_id'])=='stored' or (prior['ack_status'] is None and box and box['state'] in ('pending','retry_wait','in_flight','auth_required')):return prior['event_id']
        result=cap.capture(cap._resend_adapter_input(row),source_report_id=rid,trigger='raw_backfill',raw_snapshot=raw.load_current_snapshot(rid,engine=self.engine),force=True)
        self.check()
        if result.event_id is None and not result.eligible:raise BackfillBlocked('not_eligible')  # 보낼 수 없는 신고(삭제·적격 아님)는 분모에서 뺀다
        if result.event_id:
            with self.store.transaction() as tx:
                self.check();tx.execute('UPDATE source_journal SET capture_trigger=? WHERE event_id=?',('raw_backfill:'+run,result.event_id))
            cap.mark_personal_save(result.event_id,True)
        return result.event_id
    def receipt(self,rid,event):
        self.check()
        if self.store.meta('raw_receipt:'+event)=='stored':return 'stored'
        row=self.store.connect().execute('SELECT ack_status,blocked_reason,connection_id,writer_epoch,consent_grant_id FROM source_journal WHERE event_id=?',(event,)).fetchone()
        box=self.store.connect().execute('SELECT state FROM outbox WHERE event_id=?',(event,)).fetchone()
        if row and row['ack_status']=='stored':return 'stored'
        if row and any(row[k]!=self._scope[k] for k in ('connection_id','writer_epoch','consent_grant_id')):return 'rebind'
        if box and box['state'] in ('blocked','dead_letter'):return 'blocked'
        if not row or row['ack_status'] is not None:return 'retry'
        return 'pending'
    def wake_upload(self):
        from services import community_uploader
        community_uploader.wake()
    def release_work(self):
        if self.token:self.manager.release_rating(self.token);self.token=None
    def close(self):
        self.release_work()
        if self.session is not None:
            try:self.session.close()
            except Exception as exc:
                from core.utils.fallback import note_fallback
                note_fallback("raw_backfill.close",exc)
