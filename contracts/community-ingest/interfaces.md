# 구현 인터페이스 v1 (병렬 작업 경계 — S-17)

병렬 작업자는 아래 이름·시그니처·job ID 를 바꾸지 않는다. 바꿔야 하면 Opus 에게 요청하고 계약 버전을 올린다.

## PC (Python, safetyreport)

```python
# services/community_store.py (T0b, Opus) — 스키마·연결·공통 쿼리
class CommunityStore:
    @classmethod
    def open(cls, data_dir: str | None = None) -> "CommunityStore": ...   # <data>/community.db, 마이그레이션
    def meta(self, key: str) -> str | None: ...
    def context(self) -> dict | None: ...                                  # context 행(dict) 또는 None
    def set_context(self, **fields) -> None: ...                           # 메인 프로세스만
    def deactivate_context(self, reason: str) -> None: ...
    def rotate_dataset(self, reason: str) -> str: ...                      # 새 local_dataset_id 반환
    def next_revision(self, conn) -> int: ...                              # 트랜잭션 안에서 증가
    def raise_revision_floor(self, last_accepted: int) -> None: ...
    def acquire_lease(self, name: str, owner: str, seconds: int) -> bool: ...
    def release_lease(self, name: str, owner: str) -> None: ...
    def transaction(self): ...                                             # context manager (BEGIN IMMEDIATE)

# services/community_gate.py (T3, Opus)
class GateState(TypedDict): state: str; can_enter: bool; reasons: list[str]; verified_age: float | None
def evaluate() -> GateState: ...                          # 캐시 기반(10분), 네트워크 없음
def require_fresh(max_age: float = 60.0) -> GateState: ...  # 필요하면 동기 재검증(community-account/status)
def invalidate(reason: str) -> None: ...
def on_change(callback: Callable[[GateState], None]) -> None: ...
def refresh_now() -> GateState: ...                        # status 호출 + context 기록/비활성화

# services/community_capture.py (T4, Muse)
@dataclass(frozen=True)
class CaptureResult: event_id: str | None; event_type: str | None; eligible: bool; payload_sha256: str
def build_adapter_input(detail: dict, title_fields: dict | None, entry_value: str | None, geo: dict, progress_status: str | None) -> dict: ...
def build_payload(adapter_input: dict) -> dict: ...        # observation.md 3절 — 순수 함수
def canonical_json(obj) -> str: ...
def capture(adapter_input: dict, *, source_report_id: str, trigger: str, rebuild_run_id: str | None = None) -> CaptureResult: ...
def mark_personal_save(event_id: str, ok: bool) -> None: ...
def capture_retry_ids() -> set[str]: ...                   # community_capture_retry.json (증분 선정에 포함)
class CaptureStoreUnavailable(RuntimeError): ...           # 연속 3회 실패 → 수집 중단 신호

# services/community_uploader.py (T4, Muse)
def request_upload(trigger: str) -> dict: ...              # RunResult dict: run_id, result, counts
def wake() -> None: ...                                    # 실시간 capture 뒤 호출(같은 프로세스)
def start_background() -> None: ...; def stop_background() -> None: ...   # data_version 1초 폴링 wake 스레드
def upload_status() -> dict: ...                           # 지도 탭 패널용
def reshare_candidates() -> int: ...; def request_reshare() -> dict: ...

# services/community_schedule.py (T4, Muse)
MIDNIGHT_JOB_ID = "community-midnight-upload"; GATE_POLL_JOB_ID = "community-gate-poll"
def due_key(now_utc: datetime) -> str: ...; def next_due_at(now_utc: datetime) -> datetime: ...
def should_run(now_utc: datetime, run: dict | None) -> bool: ...
def register_community_jobs(scheduler) -> None: ...        # 멱등(replace_existing=True)
def run_midnight(now_utc: datetime | None = None) -> dict: ...   # should_run → lease → request_upload('midnight')
def catch_up_on_start() -> None: ...

# services/community_rebuild.py (T3, Opus)
def status() -> dict: ...; def start(confirmed_by: str) -> dict: ...; def resume() -> dict: ...; def pause(reason: str) -> dict: ...
def required() -> bool: ...
```

- `core/utils/scheduler.update_jobs()`(T3)는 크롤 job 만 제거·재생성하고 끝에서 `register_community_jobs(scheduler)` 를 다시 호출한다.
- `core/storage/reports_repo._save_one()`(T4)는 `_prefetch_derived()` 결과(geo)가 나온 직후, 개인 저장 트랜잭션 **전에** `capture(...)` 를 호출하고, 저장 후 `mark_personal_save` 한다. capture 가 예외를 내면 **그 신고의 개인 저장을 하지 않고** `save_crawled` 의 실패 목록에 `community_capture_failed` 로 넣는다(다음 수집이 다시 읽게 — S-03).
- `CommunityStore.rotate_dataset(reason)` 은 `services/db_backup.py` 의 복원 함수가 파일 교체 **전에** 호출한다(T3).
- `community_uploader.refresh_server_completed() -> bool`(T4)는 `community-ingest/manifest` 전 페이지로 `server_completed` 를 교체하고 `meta.manifest_scope` 를 기록한다. T3 의 게이트가 writer 등록·rebind·takeover 직후, rebuild 가 시작 때, 크롤 시작 전(scope 불일치 시) 호출한다. False 면 크롤을 시작하지 않는다.
- 증분 선정(T3 `core/database/database.py`)은 `vectors/list_refetch.json` 규칙: 기존 SQL 후보 ∪ {목록 C_NOW 라벨 ≠ `detail_status` 라벨, 또는 detail_status 없음(마지막 rebuild 의 failed_permanent 제외)} — community.db 를 읽는 파이썬 조인.
- 수집 서브프로세스(start.py)는 `CommunityStore` 를 직접 열어 capture 한다. 메인 프로세스 uploader 는 `PRAGMA data_version` 1초 폴링으로 깨어난다.
- `main.py` lifespan(T3): `CommunityStore.open()` → `community_gate.refresh_now()`(비동기) → `community_uploader.start_background()` → `register_community_jobs` → `catch_up_on_start()` → rebuild 재개 확인.

## 모바일 (Dart, safetyreport-mobile)

```dart
// lib/community/community_store.dart (T0b, Opus)
class CommunityStore { static Future<CommunityStore> open({String? path}); Future<Map<String,Object?>?> context();
  Future<void> setContext(Map<String,Object?> f); Future<void> deactivateContext(String reason);
  Future<String> rotateDataset(String reason); Future<bool> acquireLease(String name, String owner, Duration d);
  Future<void> releaseLease(String name, String owner); Future<T> transaction<T>(Future<T> Function(Transaction) f); }

// lib/community/gate/community_gate.dart (T5, Muse)
class CommunityGate extends ChangeNotifier { GateState get state; Future<GateState> refreshNow();
  Future<GateState> requireFresh({Duration maxAge = const Duration(seconds: 60)}); void invalidate(String reason); }

// lib/community/capture/community_capture.dart (T6, Muse)
Map<String,Object?> buildAdapterInput(Report report, String entryValue, GeocodeHit? geo, String progressStatus);
Map<String,Object?> buildPayload(Map<String,Object?> adapterInput);   // 순수 함수, 벡터 테스트
String canonicalJson(Object? value);
Future<CaptureResult> capture(Map<String,Object?> adapterInput, {required String sourceReportId, required String trigger, String? rebuildRunId});
Future<void> markPersonalSave(String eventId, bool ok);

// lib/community/upload/community_uploader.dart (T6, Muse)
Future<UploadRunResult> requestCommunityUpload(String trigger); void wake(); Future<UploadStatus> uploadStatus();
// lib/community/upload/community_schedule.dart (T6, Muse)
String dueKey(DateTime nowUtc); DateTime nextDueAt(DateTime nowUtc); bool shouldRun(DateTime nowUtc, Map<String,Object?>? run);
Future<void> registerBackgroundJobs(); Future<void> cancelBackgroundJobs(); Future<void> catchUp(String reason);
// lib/community/rebuild/community_rebuild.dart (T5, Muse) — SyncEngine.start(fullSync: true, rebuildRunId: …) 호출
```

- `SyncEngine`(T6)은 `start({bool fullSync, String? rebuildRunId})` 를 제공하고, rebuild 모드에서 목록 부재 행 삭제를 하지 않으며 item checkpoint 를 `rebuild_items` 에 쓴다.
- `ReportProvider.onGatePassed()`(T5)가 백그라운드 등록·큐 drain·자동 동기화·`registerBackgroundJobs()`(T6 함수)를 호출한다. 게이트 전에는 아무 것도 시작하지 않는다.
- Workmanager dispatcher(`background_login_check.dart`, T6)는 작업 이름으로 분기: 기존 `standalone-daily-login-check`, 신규 `community-upload-periodic`, `community-midnight`. 두 신규 작업은 게이트 캐시(유효)·Standalone 모드일 때만 업로드한다.
