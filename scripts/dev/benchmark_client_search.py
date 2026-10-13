"""Synthetic-only HTTP harness for mobile test/services/client_search_http_benchmark_test.dart.
SAFETYREPORT_DATA_DIR must be a new empty directory. No main app/background services.
"""
import json
import os
import sys
from pathlib import Path
from time import perf_counter
from contextvars import ContextVar

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
data = Path(os.environ['SAFETYREPORT_DATA_DIR'])
if not data.is_dir() or any(data.iterdir()):
    raise SystemExit('Use a new empty SAFETYREPORT_DATA_DIR')
os.environ['SAFETYREPORT_FIXTURE_MODE'] = '1'
(data / 'config.ini').write_text('[SETTINGS]\nexclude_withdraw = False\nuse_representative_records = True\n')
from core.database import database, models
from core.database.engine import get_engine
from core.utils import logger
from services import report_query_service as rqs
from fastapi import FastAPI, Request
from web.routers import api_route
import uvicorn

logger.LoggerFactory.create_logger(mode='crawl')
engine = get_engine()
models.metadata.create_all(engine)
vectors = json.loads((ROOT / 'contracts/client-search-vectors.json').read_text())
plans = {}
with engine.begin() as conn:
    statements = database._index_statements()
    for sql in statements:
        if '_number_id ' not in sql:
            conn.exec_driver_sql(sql)
    query = 'SELECT ID FROM mysafety_report_effective WHERE category=\'traffic\' ORDER BY 신고번호 DESC, ID DESC LIMIT 200'
    plans['before'] = [list(row) for row in conn.exec_driver_sql('EXPLAIN QUERY PLAN ' + query)]
    for sql in statements:
        conn.exec_driver_sql(sql)
    plans['after'] = [list(row) for row in conn.exec_driver_sql('EXPLAIN QUERY PLAN ' + query)]
    for start in range(0, 60000, 500):
        rows = [dict(vectors['rows'][i % 32], ID=f'{i:06}', 신고번호=f'SYN-{i//2:06}',
                     category=['traffic','parking','safety','living','other'][i % 5],
                     신고내용=(vectors['rows'][i % 32]['신고내용'] or '') + ' 합성 본문입니다.' * 40)
                for i in range(start, start+500)]
        conn.execute(models.report_effective_table.insert(), rows)
(data / 'query-plans.json').write_text(json.dumps(plans, ensure_ascii=False, indent=2))
key = database.create_api_key(engine, 'synthetic-search-benchmark')
(data / 'fixture-key.txt').write_text(key)
# Per-request service timing, separate from FastAPI conversion/serialization.
request_metrics = ContextVar('request_metrics')
original = rqs.get_report_page

def timed(*args, **kwargs):
    start = perf_counter()
    try:
        return original(*args, **kwargs)
    finally:
        request_metrics.get()['service_ms'] += (perf_counter() - start) * 1000
rqs.get_report_page = timed
app = FastAPI()
# Do not call upstream version/update services in a local benchmark.
@app.get('/api/v1/server/version', dependencies=[api_route.Depends(api_route._require_api_key)])
def version():
    return {'version':'3.0.0.0-dev','protocol_version':3,'supported_client_protocols':[3], 'report_taxonomy':5}
app.include_router(api_route.router)

@app.middleware('http')
async def timing(request: Request, call_next):
    metrics = {'service_ms': 0.0}
    request_metrics.set(metrics)
    start = perf_counter()
    response = await call_next(request)
    response.headers['X-Bench-Response-Ms'] = str((perf_counter()-start)*1000)
    response.headers['X-Bench-Service-Ms'] = str(metrics['service_ms'])
    return response

if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('SR_SEARCH_PORT','18764')), log_level='warning')
