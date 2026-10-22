"""Per-item backfill persistence; metadata never contains the full inventory."""
import json

SCHEMA="""
CREATE TABLE IF NOT EXISTS raw_backfill_items (
 run_id TEXT NOT NULL, report_id TEXT NOT NULL, position INTEGER NOT NULL,
 state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, eligible INTEGER NOT NULL DEFAULT 0,
 was_missing INTEGER NOT NULL DEFAULT 0, event_id TEXT, filled INTEGER NOT NULL DEFAULT 0,
 unfilled INTEGER NOT NULL DEFAULT 0, detail_json TEXT NOT NULL DEFAULT '{}',
 PRIMARY KEY(run_id,report_id));
CREATE INDEX IF NOT EXISTS raw_backfill_position ON raw_backfill_items(run_id,position);
CREATE INDEX IF NOT EXISTS raw_backfill_state ON raw_backfill_items(run_id,state,position);
CREATE TABLE IF NOT EXISTS raw_backfill_supplements (
 local_dataset_id TEXT NOT NULL,report_id TEXT NOT NULL,kind TEXT NOT NULL,status TEXT NOT NULL,
 PRIMARY KEY(local_dataset_id,report_id,kind));
CREATE INDEX IF NOT EXISTS source_journal_logical ON source_journal(source_report_id,dataset_key,contributor_fingerprint);
"""
KEYS=('state','attempts','eligible','was_missing','event_id','filled','unfilled')
class SqliteItems:
    def __init__(self,store,check=lambda:None):self.store,self.check=store,check
    def _values(self,item):return tuple(int(item.get(k,0)) if k in ('eligible','was_missing','attempts','filled','unfilled') else item.get(k) for k in KEYS)+(json.dumps({k:v for k,v in item.items() if k not in KEYS}),)
    def _item(self,row):
        return {**json.loads(row['detail_json']),**{k:bool(row[k]) if k in ('eligible','was_missing') else row[k] for k in KEYS}}
    def reset(self,run,items):
        with self.store.transaction() as tx:
            self.check();tx.execute('DELETE FROM raw_backfill_items WHERE run_id=? OR run_id!=?',(run,run))  # 이전 run 의 항목은 남기지 않는다(표가 run 마다 늘지 않게)
            tx.executemany('INSERT INTO raw_backfill_items VALUES (?,?,?,?,?,?,?,?,?,?,?)',((run,rid,n,*self._values(item)) for n,(rid,item) in enumerate(items)))
    def load_legacy(self,run,items):
        if self.size(run)==0:self.reset(run,items.items())
    def retry(self,run):
        """'다시 시도' 버튼: 시각과 관계없이 모든 보류를 처음부터(attempts·hold_count 0, stale 해제)."""
        with self.store.transaction() as tx:
            self.check();tx.execute("UPDATE raw_backfill_items SET state='pending',attempts=0,event_id=NULL,"
                "detail_json=json_remove(json_set(detail_json,'$.hold_count',0,'$.stale',json('false')),'$.next_attempt_at')"
                " WHERE run_id=? AND state='hold'",(run,))
    def release_due_holds(self,run,now):
        """다시 시도할 시각이 지난 보류를 pending(attempts=2)으로. hold_count·first_held_at 은 남겨 다음 간격을 늘린다.
        시각이 없는 옛 보류(이번 버전 전)도 지난 것으로 본다."""
        with self.store.transaction() as tx:
            self.check()
            return tx.execute("UPDATE raw_backfill_items SET state='pending',attempts=2,event_id=NULL WHERE run_id=? AND state='hold'"
                " AND IFNULL(json_extract(detail_json,'$.next_attempt_at'),0)<=?",(run,now)).rowcount
    def next_hold_at(self,run):
        row=self.store.connect().execute("SELECT MIN(IFNULL(json_extract(detail_json,'$.next_attempt_at'),0)) FROM raw_backfill_items"
            " WHERE run_id=? AND state='hold'",(run,)).fetchone()
        return row[0] if row else None
    def counts(self,run):
        """진행 보고 개수(규칙 문서 4-1): run 항목 수·상태별 수·30일 넘은 보류 수."""
        r=self.store.connect().execute("""SELECT COUNT(*) total,COUNT(CASE WHEN state='stored' THEN 1 END) stored,
        COUNT(CASE WHEN state='queued' THEN 1 END) queued,COUNT(CASE WHEN state='pending' THEN 1 END) pending,
        COUNT(CASE WHEN state='hold' THEN 1 END) holds,
        COUNT(CASE WHEN state='hold' AND json_extract(detail_json,'$.stale') IN (1,'true') THEN 1 END) stale_holds,
        COUNT(CASE WHEN state='raw_unavailable' THEN 1 END) raw_unavailable,COUNT(CASE WHEN state='not_eligible' THEN 1 END) not_eligible
        FROM raw_backfill_items WHERE run_id=?""",(run,)).fetchone()
        return dict(r)
    def page(self,run,cursor,limit):
        return [(r['position'],r['report_id'],self._item(r)) for r in self.store.connect().execute('SELECT * FROM raw_backfill_items WHERE run_id=? AND position>=? ORDER BY position LIMIT ?',(run,cursor,limit))]
    def queued(self,run,limit):
        return [(r['report_id'],self._item(r)) for r in self.store.connect().execute("SELECT * FROM raw_backfill_items WHERE run_id=? AND state='queued' ORDER BY position LIMIT ?",(run,limit))]
    def put(self,run,rid,item):
        with self.store.transaction() as tx:
            self.check();tx.execute('UPDATE raw_backfill_items SET state=?,attempts=?,eligible=?,was_missing=?,event_id=?,filled=?,unfilled=?,detail_json=? WHERE run_id=? AND report_id=?',(*self._values(item),run,rid))
    def put_many(self,run,entries):
        """여러 항목을 한 트랜잭션(한 번의 fsync)으로 쓴다 — 항목마다 commit 하지 않는다."""
        if not entries:return
        with self.store.transaction() as tx:
            self.check()
            tx.executemany('UPDATE raw_backfill_items SET state=?,attempts=?,eligible=?,was_missing=?,event_id=?,filled=?,unfilled=?,detail_json=? WHERE run_id=? AND report_id=?',
                ((*self._values(item),run,rid) for rid,item in entries))
    def size(self,run):return self.store.connect().execute('SELECT COUNT(*) FROM raw_backfill_items WHERE run_id=?',(run,)).fetchone()[0]
    def summary(self,run):
        r=self.store.connect().execute("""SELECT COUNT(CASE WHEN eligible=1 AND state!='not_eligible' THEN 1 END) total,
        COUNT(CASE WHEN eligible=1 AND state='raw_unavailable' THEN 1 END) raw_unavailable,
        COUNT(CASE WHEN eligible=1 AND state='stored' THEN 1 END) sent,
        COALESCE(SUM(filled),0) filled,COALESCE(SUM(unfilled),0) unfilled,COALESCE(SUM(was_missing),0) missing_reports,
        COUNT(CASE WHEN state='hold' THEN 1 END) holds,
        COUNT(CASE WHEN state NOT IN ('stored','raw_unavailable','not_eligible') THEN 1 END) unfinished
        FROM raw_backfill_items WHERE run_id=?""",(run,)).fetchone()
        result=dict(r);result['available']=result['total']-result['raw_unavailable'];return result
    def holds(self,run):return self.summary(run)['holds']
