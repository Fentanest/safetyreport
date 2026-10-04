"""종료 이벤트 재연결용 로컬 저널. 교환 DB/기기별 변경 커서와 분리한다."""
import json
import os
import sqlite3
import threading
from contextlib import closing

import settings.settings as settings

_lock = threading.RLock()
RETENTION = 256


def _open():
    os.makedirs(settings.datapath, exist_ok=True)
    path = os.path.join(settings.datapath, 'ws_terminal_events.db')
    conn = sqlite3.connect(path, timeout=5)
    os.chmod(path, 0o600)
    conn.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT UNIQUE, payload TEXT NOT NULL)')
    return conn


def append(event):
    # 완료 이벤트만 기록한다. 큰 변경 목록은 기존 crawl/results 저장 계약을 쓴다.
    with _lock, closing(_open()) as conn, conn:
        run_id = event['data'].get('run_id')
        conn.execute('INSERT OR IGNORE INTO events(run_id,payload) VALUES (?,?)',
                     (run_id, json.dumps(event, ensure_ascii=False)))
        row = conn.execute('SELECT seq,payload FROM events WHERE run_id IS ? ORDER BY seq DESC LIMIT 1', (run_id,)).fetchone()
        conn.execute('DELETE FROM events WHERE seq <= (SELECT MAX(seq) - ? FROM events)', (RETENTION,))
        return {**json.loads(row[1]), 'event_id': row[0]}


def replay(after=None):
    with _lock, closing(_open()) as conn, conn:
        first, latest = conn.execute('SELECT MIN(seq),MAX(seq) FROM events').fetchone()
        latest = latest or 0
        reset = after is not None and after > latest
        gap = after is not None and first is not None and after < first - 1
        cursor = 0 if reset else after
        rows = [] if cursor is None else conn.execute('SELECT seq,payload FROM events WHERE seq>? ORDER BY seq', (cursor,)).fetchall()
        return {'latest_event_id': latest, 'replay_gap': gap, 'cursor_reset': reset}, [
            {**json.loads(payload), 'event_id': seq} for seq, payload in rows]
