"""FIX-P 브라우저 증거용 fixture 서버: 필수 버전 잠금(client_lock)을 넣고 띄운다. 합성 자료, 외부 네트워크 없음(fixture 모드).

python serve_fixp.py <repo> <data-dir> <port> <min_version>
- min_version 이 지금 VERSION 보다 높으면 잠금 유지(카카오 로그인 복구 경로 확인), 같거나 낮으면 읽을 때 풀린다(D3).
"""
import json, sys, time
from pathlib import Path
root = Path(sys.argv[1]); data = Path(sys.argv[2]); port = int(sys.argv[3]); minimum = sys.argv[4]
sys.path.insert(0, str(root)); sys.path.insert(0, str(root / 'scripts/dev'))
import fixture_server as fx
data_dir = fx.prepare_data_dir(str(data), reset=True); fx.activate_environment(data_dir); fx.seed(data_dir)
from services.community_store import CommunityStore
store = CommunityStore.open(str(data_dir))
store.set_meta('client_lock', json.dumps({'locked': True, 'min_version': minimum, 'url': None,
                                          'message': '<b>서버 안내</b> 업데이트 뒤 다시 켜 주세요.', 'decided_at': int(time.time())},
                                         ensure_ascii=False))
print('[fixp] client_lock min_version', minimum, flush=True)
fx.serve(data_dir, '127.0.0.1', port)
