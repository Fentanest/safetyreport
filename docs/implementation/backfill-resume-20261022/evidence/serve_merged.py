"""합친 사본(C+E)으로 fixture 서버: 공지 캐시·중지·1회 전송 marker(공식 로그인 필요)를 넣고 띄운다. 합성 자료."""
import json, sys, time
from pathlib import Path
root = Path(sys.argv[1]); data = Path(sys.argv[2]); port = int(sys.argv[3])
sys.path.insert(0, str(root)); sys.path.insert(0, str(root / 'scripts/dev'))
import fixture_server as fx
data_dir = fx.prepare_data_dir(str(data), reset=True); fx.activate_environment(data_dir); fx.seed(data_dir)
from services.community_store import CommunityStore
store = CommunityStore.open(str(data_dir))
now = time.time()
store.set_meta('upload_pause', json.dumps({'paused': True, 'resume_at': None, 'message': None, 'source': 'status', 'saved_at': now, 'wake_at': None, 'fallback_at': now + 600}))
store.set_meta('raw_backfill', json.dumps({'run_id': 'run-fixture', 'local_dataset_id': store.local_dataset_id(), 'state': 'paused', 'phase': 'filling',
                                           'reason': 'official_login_required', 'login_fp': 'fp', 'next_attempt_at': now + 10**8}))
from services import app_announcements as ann
payload = json.loads((root / 'contracts/app-announcements/examples/response.json').read_text())
pass
items = ann.parse_response(payload)
ann._cache.update(fetched_at=time.time(), items=items)
print('[merged] announcements', [i['id'] for i in items], flush=True)
fx.serve(data_dir, '127.0.0.1', port)
