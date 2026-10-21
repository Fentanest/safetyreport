"""크롤링 결과 저장과 화면용 표(effective) 만들기 (서버 스키마 6, docs/plans/report-taxonomy-plan.md).

규칙
- 신고 한 건 = mysafety_report 한 행. 카테고리는 열 값(services/report_category.resolve)이고 표를 옮기지 않는다.
- 열 주인(계약 owner): 크롤러는 사이트 열(site)만 쓴다. 사용자 수정값은 mysafety_report_override 에 있고 재크롤링이 지우지 않는다.
- 신고 1건 = 트랜잭션 1개: 신고 행·본문·화면용 표(그 신고만)를 함께 커밋한다(S-12).
  네트워크(카카오 지오코딩, 사진 촬영 시각)는 트랜잭션을 열기 전에 끝낸다(S-7·S-8).
- 변경 판정은 모바일 `_syncedAtTrackedKeys` 와 같은 열(상세 사이트 열)과 category·entry_value·SSNPC 코드·본문만 보고,
  NULL 과 '' 는 같게 본다(둘 다 '값 없음' — S-5·S-6). 지오코딩·사진 열은 변경으로 치지 않는다.
- 목록의 식별 정보(상태·신고번호·신고명·신고일)는 빈 값으로 덮지 않는다(S-23). 만족도·별점은 결정 D-2·D-3.
- category NULL = 목록에서만 본 신고(상세 미저장). 화면용 표 = category 가 있는 신고 + 수정값 + 감시목록(계산)
  + 6개월 지난 첨부 가림. 전체 재생성과 1건 갱신이 같은 규칙이다(S-31).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from datetime import datetime

from dateutil.relativedelta import relativedelta
from sqlalchemy import case, delete, func, select, update
from sqlalchemy.dialects.sqlite import insert

from core.database import models
from core.utils import logger
from core.utils.fallback import note_fallback
from services import report_category

REPORT = models.report_table
EFFECTIVE = models.report_effective_table
CATEGORIES = report_category.CATEGORIES
GEO_COLUMNS = ("주소정규화", "행정구역", "위도", "경도", "지오코딩상태")
PHOTO_COLUMNS = ("사진_첫촬영", "사진_끝촬영", "사진_촬영수")
DERIVED_DETAIL_COLUMNS = frozenset(GEO_COLUMNS) | frozenset(PHOTO_COLUMNS) | {"synced_at"}
DETAIL_SITE_COLUMNS = tuple(c for c in models.DETAIL_COLUMN_NAMES if c not in DERIVED_DETAIL_COLUMNS)
# 모바일 LocalDbService._syncedAtTrackedKeys 와 같은 목록(category·entry_value·SSNPC·본문은 따로 비교). 계약의 change_tracked 와 테스트로 묶는다.
CHANGE_TRACKED_COLUMNS = DETAIL_SITE_COLUMNS
TITLE_IDENTITY_COLUMNS = ("상태", "신고번호", "신고명", "신고일")
#: 재파싱 공유 캡처 입력에 쓰는 목록 열. 일반 크롤의 title_fields 에 들어가는 열 전부
#: (`models.TITLE_COLUMN_NAMES` 에서 계산 표시값인 감시목록 제외 — `_title_values` 가
#: DB 에 쓰는 키와 같은 집합이다). DB 목록 열 자체는 재파싱이 갱신하지 않는다.
REPARSE_CAPTURE_TITLE_COLUMNS = ("상태", "신고번호", "신고명", "신고일", "만족도조사여부", "별점", "별점사유")
OVERRIDABLE_COLUMNS = frozenset(DETAIL_SITE_COLUMNS)
ATTACHMENT_COLUMNS = ("지도", "첨부사진", "첨부파일")
EXPIRED_ATTACHMENT = "6개월 초과"


def _now_ms() -> int:
    return int(datetime.now().timestamp() * 1000)


def comparable(value) -> str:
    """변경 판정용. NULL 과 '' 는 같게, 3.0 과 3 은 같게."""
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return ""
        if value.is_integer():
            return str(int(value))
    return str(value)


def _clean(value):
    """pandas 가 빈 칸을 NaN 으로 줄 때가 있어 None 으로 돌린다. 그 밖의 값은 그대로."""
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


@dataclass
class CrawledDetail:
    id: str
    category: str
    detail: dict
    entry_value: str | None = None
    progress_status: str | None = None
    title_fields: dict | None = None
    raw_content: str | None = None
    raw_type: str = ""
    ssnpc_cd: str | None = None
    ssnpc_type: str | None = None
    # 상세 원본(2026-10-17 명세 1-1·1-3): 정규 직렬화 바이트와 sha·크기·받은 경로.
    # 크롤러가 실어 보내면 _save_one 의 같은 트랜잭션에서 원본 표에 upsert 한다. 없으면 원본 표는 손대지 않는다.
    raw_canonical: bytes | None = None
    raw_sha256: str | None = None
    raw_size: int | None = None
    raw_source: str | None = None

    def __post_init__(self):
        self.ssnpc_cd, self.ssnpc_type = report_category.normalize_code(self.ssnpc_cd, self.ssnpc_type)
        if self.ssnpc_cd or self.entry_value is not None:
            self.category = report_category.category_of(self.ssnpc_cd, self.ssnpc_type, self.entry_value)
        elif self.category not in CATEGORIES:
            self.category = "other"

    @classmethod
    def from_legacy_tuple(cls, item) -> "CrawledDetail":
        """크롤러 튜플 (df, category, entry_value, progress_status, title_fields, raw_content, raw_type, ssnpc_cd, ssnpc_type[, raw_info]).
        뒤쪽 원소는 없어도 된다(2~10개). 10번째 raw_info 는 detail_pipeline.build_detail_result 가 붙인
        {"raw_canonical": bytes, "raw_sha256": str, "raw_size": int, "raw_source": str} 이다."""
        fields = list(item) + [None] * (10 - len(item))
        df, category, entry_value, progress_status, title_fields, raw_content, raw_type, ssnpc_cd, ssnpc_type, raw_info = fields[:10]
        rows = df.to_dict("records")
        if not rows:
            raise ValueError("빈 상세 데이터")
        row = {k: _clean(v) for k, v in rows[0].items()}
        raw = _coerce_raw_info(raw_info)
        return cls(
            id=str(row["ID"]), category=category or "other", detail=row,
            entry_value=entry_value, progress_status=progress_status, title_fields=title_fields,
            raw_content=raw_content, raw_type=raw_type or "", ssnpc_cd=ssnpc_cd, ssnpc_type=ssnpc_type,
            raw_canonical=raw.get("raw_canonical"), raw_sha256=raw.get("raw_sha256"),
            raw_size=raw.get("raw_size"), raw_source=raw.get("raw_source"),
        )


def _coerce_raw_info(raw_info) -> dict:
    """10번째 원소를 검증해 돌려준다. 없거나 모양이 틀리면 빈 dict(원본 표는 손대지 않음)."""
    if not isinstance(raw_info, dict):
        return {}
    canonical = raw_info.get("raw_canonical")
    sha = raw_info.get("raw_sha256")
    size = raw_info.get("raw_size")
    source = raw_info.get("raw_source") or "crawl"
    if not isinstance(canonical, (bytes, bytearray)) or not isinstance(sha, str) or not isinstance(size, int):
        return {}
    if size != len(canonical):
        return {}
    return {"raw_canonical": bytes(canonical), "raw_sha256": sha, "raw_size": size, "raw_source": source}


def _save_raw(conn, rec: CrawledDetail) -> None:
    """상세 원본을 같은 트랜잭션에서 원본 표에 upsert 한다(같은 ID 로 다시 받으면 덮어씀).

    크롤러가 원본을 실어 보내지 않은 저장(옛 튜플·수동 경로)이면 아무것도 하지 않는다.
    """
    if rec.raw_canonical is None or rec.raw_sha256 is None or rec.raw_size is None:
        return
    from core.utils import raw_detail as _raw

    table = models.report_detail_raw_table
    blob = _raw.compress(bytes(rec.raw_canonical))
    values = {"ID": rec.id, "raw_zlib": blob, "raw_sha256": rec.raw_sha256,
              "raw_size": rec.raw_size, "fetched_at": _raw.utc_now_fetched_at(),
              "source": rec.raw_source or "crawl"}
    stmt = insert(table).values(**values)
    conn.execute(stmt.on_conflict_do_update(
        index_elements=["ID"],
        set_={c: values[c] for c in ("raw_zlib", "raw_sha256", "raw_size", "fetched_at", "source")},
    ))


def _write_raw_file_best_effort(rec: CrawledDetail) -> None:
    """원본 파일(`<datapath>/raw_details/<ID>.json`)을 새로 쓴다. 실패해도 DB 저장은 그대로 둔다."""
    if rec.raw_canonical is None:
        return
    from core.utils import raw_detail as _raw

    try:
        sanitized = json.loads(bytes(rec.raw_canonical).decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        note_fallback("reports_repo.raw_file_decode", exc)
        return
    if not isinstance(sanitized, dict):
        return
    _raw.write_raw_file(rec.id, sanitized)


@dataclass
class SaveResult:
    changed: list[dict] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)
    saved: int = 0


# ── 저장 ─────────────────────────────────────────────────────────────────────

def _find_row(conn, record_id: str) -> dict | None:
    """신고 행(목록만 본 행 포함). 없으면 None."""
    row = conn.execute(select(REPORT).where(REPORT.c.ID == record_id)).mappings().first()
    return dict(row) if row is not None else None


def _has_detail(row: dict | None) -> bool:
    return row is not None and row.get("category") is not None


def _prefetch_derived(engine, rec: CrawledDetail, existing: dict | None) -> dict:
    """트랜잭션 밖에서 공식 좌표·사진 촬영 시각을 준비한다."""
    from services import geocode_service, photo_capture_time

    address = rec.detail.get("위반장소") or ""
    geo = geocode_service.official_geo_payload(address, rec.detail.get("위도"), rec.detail.get("경도"))

    photo = {name: (existing or {}).get(name) for name in PHOTO_COLUMNS}
    if photo["사진_촬영수"] is None and photo_capture_time.is_parking_report(rec.category, rec.entry_value):
        try:
            collected = photo_capture_time.collect(rec.detail.get("첨부사진"))
        except Exception as exc:  # fixture 차단 포함 — 크롤링은 멈추지 않는다
            logger.LoggerFactory.logbot.warning(f"[photo] ID {rec.id} 촬영 시각 수집 실패: {exc}")
            collected = None
        if collected is not None:
            photo = collected
    return {**{c: geo.get(c) for c in GEO_COLUMNS}, **photo}


def _save_body(conn, rec: CrawledDetail, now_ms: int) -> bool:
    if rec.raw_content is None or not str(rec.raw_content).strip():
        return False
    table = models.report_body_table
    previous = conn.execute(select(table).where(table.c.ID == rec.id)).mappings().first()
    content, raw_type = str(rec.raw_content), str(rec.raw_type or "")
    changed = previous is not None and (previous["raw_content"] != content or (previous["raw_type"] or "") != raw_type)
    saved_at = now_ms if previous is None or changed or previous["saved_at"] is None else previous["saved_at"]
    stmt = insert(table).values(ID=rec.id, raw_content=content, raw_type=raw_type, saved_at=saved_at)
    conn.execute(stmt.on_conflict_do_update(index_elements=["ID"], set_={"raw_content": content, "raw_type": raw_type, "saved_at": saved_at}))
    return changed


def _classification(rec: CrawledDetail, existing: dict | None) -> dict:
    """이번 응답에 없는 코드·메뉴(None)는 저장된 값을 지우지 않는다. category 는 합친 값으로 다시 정한다."""
    old = existing or {}
    entry_value = rec.entry_value if rec.entry_value is not None else old.get("entry_value")
    cd, type_ = (rec.ssnpc_cd, rec.ssnpc_type) if rec.ssnpc_cd else (old.get("ssnpc_cd"), old.get("ssnpc_type"))
    if cd or entry_value is not None:
        category = report_category.category_of(cd, type_, entry_value)
    else:
        category = rec.category
    return {"category": category, "entry_value": entry_value, "ssnpc_cd": cd, "ssnpc_type": type_}


def _classification_changed(existing: dict, cls: dict) -> bool:
    if existing.get("category") != cls["category"]:
        return True
    # 이전 값이 있을 때만 변경으로 본다(처음 채워지는 코드는 변경 알림 대상이 아님 — 스키마 6 이행 직후 가짜 변경 방지).
    return any(existing.get(k) is not None and comparable(existing.get(k)) != comparable(cls[k])
               for k in ("entry_value", "ssnpc_cd", "ssnpc_type"))


def _title_values(rec: CrawledDetail) -> dict:
    """상세 응답의 목록 필드 → 갱신할 값(SQL 식 포함). 비어 있는 식별 정보는 넣지 않는다."""
    from core.database.database import _TITLE_STATUS_FROM_PROGRESS  # 레거시 경로(title_fields 없음)의 상태 대응표

    fields = rec.title_fields
    if not fields:
        status = _TITLE_STATUS_FROM_PROGRESS.get(rec.progress_status)
        return {"상태": case((REPORT.c.상태 == "진행", status), else_=REPORT.c.상태)} if status else {}
    values = {c: fields[c] for c in TITLE_IDENTITY_COLUMNS if fields.get(c) not in (None, "")}
    poll = fields.get("만족도조사여부") or ""
    # 별점: 상세 응답의 점수(파서)나 만족도 조회 결과(detail_pipeline)가 있을 때만 붙는다. 사유는 조회가 성공했을 때만 붙는다.
    has_rating = "별점" in fields
    if has_rating and poll == "참여 가능":
        values["만족도조사여부"] = poll  # 확정 미참여 재분류(조회가 미참여를 확정)
    elif poll:
        values["만족도조사여부"] = case((REPORT.c.만족도조사여부 == "참여 완료", REPORT.c.만족도조사여부), else_=poll)
    if has_rating:  # 결정 D-2: 사이트 값이 있을 때만 바꾼다
        values["별점"] = fields.get("별점")
    if "별점사유" in fields:  # 조회 실패면 사유 키가 없다 → 기존 사유 유지
        values["별점사유"] = fields.get("별점사유")
    return values


class _CaptureFailed(RuntimeError):
    """공유 DTO 확정 실패 — 그 신고의 개인 저장을 건너뛰라는 신호."""


def _community_capture_for(rec: CrawledDetail, derived: dict, existing: dict | None = None,
                             *, trigger: str | None = None, engine=None):
    """개인 DB 쓰기 잠금 안에서, 원문 쓰기 전에 공유 DTO 를 확정한다. 실패하면 개인 저장을 롤백한다.

    fine_estimate(2026-10-12) 입력: 사진 촬영 시각은 detail 이 아니라 _prefetch_derived() 결과(`derived`)에 있고,
    신고명은 이번 응답의 title_fields 에 없을 수 있어(상세만 다시 받은 경우) 기존 저장 행(`existing`)으로 떨어진다 —
    둘 다 build_adapter_input 에 없으면 capture 가 fine_estimate 판정을 못 한다(fine-estimate-contract.md).
    """
    import os as _os

    from services import community_capture as _cap

    geo = {c: derived.get(c) for c in GEO_COLUMNS}
    detail = dict(rec.detail or {})
    title_fields = dict(rec.title_fields or {}) if rec.title_fields else None
    report_name = (title_fields or {}).get("신고명") or (existing or {}).get("신고명")
    adapter = _cap.build_adapter_input(detail, title_fields, rec.entry_value, geo, rec.progress_status,
                                       ssnpc_cd=rec.ssnpc_cd, ssnpc_type=rec.ssnpc_type,
                                       report_name=report_name,
                                       photo_first_at=derived.get("사진_첫촬영"), photo_last_at=derived.get("사진_끝촬영"))
    if trigger is None:
        trigger = _os.environ.get("SAFETYREPORT_REBUILD_RUN_ID") and "rebuild" or "realtime"
    run_id = _os.environ.get("SAFETYREPORT_REBUILD_RUN_ID") or None
    try:
        _cap.add_retry_id(rec.id, "capture_started")
    except Exception as exc:
        raise _cap.CaptureStoreUnavailable(f"capture retry intent unwritable: {exc}") from exc
    raw_snapshot = None
    try:
        # v2 로 선택된 때에만 원본 스냅숏을 만든다. 어떤 실패도 개인 저장을 막지 않는다(capture 가 raw_required 로 보류한다).
        from services import community_raw_ingest as raw
        store = _cap._store(None)
        context, _authorization = _cap.read_account_context(store)
        if raw.selected(store, raw.ingest_status(store), context):
            from core.utils import raw_detail as _raw
            body, fetched, source = rec.raw_canonical, _raw.utc_now_fetched_at(), rec.raw_source or 'crawl'
            if body is None and engine is not None:
                with engine.connect() as raw_conn:
                    row = raw_conn.execute(select(models.report_detail_raw_table).where(models.report_detail_raw_table.c.ID == rec.id)).mappings().first()
                if row is not None:
                    try:
                        body = _raw.verify(row['raw_zlib'], row['raw_sha256'])
                        fetched, source = row['fetched_at'], row['source']
                    except ValueError:
                        body = None
            if body is not None:
                fields = {**(existing or {}), **(title_fields or {}), **derived,
                          'entry_value': rec.entry_value, 'ssnpc_cd': rec.ssnpc_cd, 'ssnpc_type': rec.ssnpc_type}
                raw_snapshot = raw.snapshot(body, fetched, source, fields)
    except Exception as exc:
        note_fallback('reports_repo.raw_snapshot', exc)
    result = _cap.capture(adapter, source_report_id=str(rec.id), trigger=trigger, rebuild_run_id=run_id, raw_snapshot=raw_snapshot)
    try:
        _cap.remove_retry_id(rec.id)
    except Exception as exc:
        note_fallback("reports_repo.remove_capture_retry", exc)
    return result


def _save_one(engine, rec: CrawledDetail, *, admission=None, capture_community=True) -> dict | None:
    with engine.connect() as read:
        existing_before = _find_row(read, rec.id)
    rec.category = _classification(rec, existing_before)["category"]
    derived = _prefetch_derived(engine, rec, existing_before)
    from services import community_capture as _cap

    capture_result = None
    try:
        with engine.begin() as conn:
            # 크롤러는 별도 프로세스다. 캡처 → 개인 저장 사이에 재전송이 끼어들지 않게
            # SQLite 쓰기 잠금을 먼저 잡고, 복원 장벽이 보는 연결도 커밋까지 유지한다.
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            if admission is not None: admission()
            existing = _find_row(conn, rec.id)
            # 사진 보충이 prefetch 동안 끝났으면 그 시각을 오래된 None 으로 덮지 않는다.
            if (existing and existing.get("사진_촬영수") is not None
                    and existing.get("첨부사진") == rec.detail.get("첨부사진")):
                derived.update({c: existing.get(c) for c in PHOTO_COLUMNS})
            try:
                capture_result = _community_capture_for(rec, derived, existing, engine=engine) if capture_community else None
            except _cap.CaptureStoreUnavailable:
                raise
            except Exception as exc:
                raise _CaptureFailed("community_capture_failed") from exc
            if admission is not None: admission()
            now_ms = _now_ms()
            site = {c: _clean(rec.detail.get(c)) for c in DETAIL_SITE_COLUMNS}
            cls = _classification(rec, existing)
            raw_changed = _save_body(conn, rec, now_ms)
            _save_raw(conn, rec)

            if not _has_detail(existing):
                change = {"id": rec.id, "change_type": "신규"}
            elif (raw_changed or _classification_changed(existing, cls)
                  or any(comparable(existing.get(c)) != comparable(site[c]) for c in CHANGE_TRACKED_COLUMNS)):
                change = {"id": rec.id, "change_type": "변경"}
            else:
                change = None

            row = {**site, **derived, **cls, "synced_at": now_ms if change else existing.get("synced_at")}
            if existing is None:
                identity = {c: (rec.title_fields or {}).get(c) for c in TITLE_IDENTITY_COLUMNS}
                conn.execute(REPORT.insert().values(ID=rec.id, **identity, 감시목록="N", **row))
            else:
                conn.execute(update(REPORT).where(REPORT.c.ID == rec.id).values(**row))
            title = _title_values(rec)
            if title:
                conn.execute(update(REPORT).where(REPORT.c.ID == rec.id).values(**title))
            refresh_effective_rows(conn, [rec.id])
    except Exception:
        if capture_result is not None:
            try:
                _cap.mark_personal_save(capture_result.event_id, False)
            except Exception as exc:
                note_fallback("reports_repo.mark_personal_save_failed", exc)
        raise
    try:
        # capture_community=False(원본 일괄 전송이 따로 enqueue 하는 저장)면 capture_result 가 없다.
        if capture_result is not None:
            _cap.mark_personal_save(capture_result.event_id, True)
        _cap.remove_retry_id(rec.id)
    except Exception as exc:
        note_fallback("reports_repo.mark_personal_save_ok", exc)
    try:
        # 원본 파일은 DB 커밋 뒤 별도로 쓴다. 어떤 실패도 예외를 밖으로 내지 않는다
        # (DB 는 저장됐는데 저장 실패로 보고되는 일이 없게 — 명세 1-4).
        _write_raw_file_best_effort(rec)
    except Exception as exc:
        note_fallback("reports_repo.raw_file_unexpected", exc)
    if change is not None:
        change["category"] = cls["category"]
        if existing is not None and _has_detail(existing) and existing.get("category") != cls["category"]:
            change["previous_category"] = existing.get("category")
    return change


class _ReparseSkipped(RuntimeError):
    """DB 최적화 재파싱에서 그 신고를 건너뛰라는 신호(사유 문자열을 담는다)."""


def reparse_one(engine, rec: CrawledDetail, *, trigger: str = "reparse",
                expected: dict | None = None) -> dict | None:
    """DB 최적화(services/db_optimize.py)가 쓰는 파서 열만 갱신 저장.

    `_save_one` 과 같은 캡처→개인 저장 잠금(`BEGIN IMMEDIATE`)·공유 캡처·화면용 표 갱신을 쓰지만,
    목록 크롤이 채우는 열(상태·신고번호·신고명·신고일·만족도조사여부·별점·별점사유)은 손대지 않는다.
    좌표(GEO_COLUMNS 5개)는 파서가 만드는 값이라 일반 상세 저장과 같은 함수·같은 규칙
    (`geocode_service.official_geo_payload(위반장소, 위도, 경도)`)으로 파싱 결과에서 계산해
    쓰기 잠금 안에서 쓴다. 사진 열(PHOTO_COLUMNS 3개)만 읽지도 덮어쓰지도 않는다 —
    UPDATE 문에 포함하지 않고(현재 값 유지), 공유 캡처 입력에 쓸 사진 현재값은 쓰기 잠금 안에서 읽는다.
    사용자 수정값(`mysafety_report_override`)은 `refresh_effective_rows` 가 그대로 다시 얹는다.
    공유 payload 가 바뀌었을 때만 `capture()` 가 새 `completed_observation` 을 만든다(바뀌지 않으면 이벤트 없음).
    `rec` 에 원본 바이트가 실려 있으면(원본 표에 없던 신고를 다시 받아온 경우) 같은 트랜잭션에서 원본 표에
    upsert 한다. 이미 원본 표에 있는 신고는 원본 표를 손대지 않는다(`_save_raw` 가 None 이면 건너뜀).

    반환: 바뀐 신고의 change dict, 바뀌지 않았으면 None. 신고 행이 없으면 _ReparseSkipped.

    `expected` 는 최적화가 시도 시작 때 읽은 행 값(답변일·처리내용·상태)이다. 쓰기 잠금
    안에서 그 행이 같은지 다시 확인하고(명세 7-4), 다르면 저장하지 않고
    `_ReparseSkipped("changed_during_reparse")` 로 끝낸다.
    """
    from dataclasses import replace

    from services import community_capture as _cap
    from services import geocode_service

    capture_result = None
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            # 쓰기 잠금 안에서 현재 행을 읽는다 — 잠금 전에 읽은 옛 값으로 덮지 않게.
            existing = _find_row(conn, rec.id)
            if existing is None:
                raise _ReparseSkipped("no_report")
            if expected is not None:
                for column in ("답변일", "처리내용", "상태"):
                    if comparable(existing.get(column)) != comparable(expected.get(column)):
                        raise _ReparseSkipped("changed_during_reparse")
            # 공유 캡처 입력의 목록 값은 쓰기 잠금 안에서 읽은 기존 행으로 채운다(일반 크롤의
            # title_fields 와 같은 집합). 재파싱 파서 출력에는 만족도 조회 값(별점·별점사유 등)이
            # 없어 그대로 쓰면 같은 원본도 payload 가 달라져 새 completed_observation 이 나가고
            # 공유 최신값(특히 별점)을 덮는다. DB 목록 열 자체는 갱신하지 않는다(아래 UPDATE 에 없음).
            capture_rec = replace(
                rec,
                title_fields={c: existing[c] for c in REPARSE_CAPTURE_TITLE_COLUMNS
                              if existing.get(c) not in (None, "")},
                progress_status=existing.get("상태") or rec.progress_status,
            )
            # GEO는 일반 저장(_prefetch_derived)과 같은 함수·같은 규칙으로 파싱 결과에서 계산해 쓴다.
            # 좌표는 파서가 만드는 값(상세 응답 C_A_W/C_A_E, 보완 시 SPLMNT_ 좌표)이다.
            geo = geocode_service.official_geo_payload(
                rec.detail.get("위반장소") or "", rec.detail.get("위도"), rec.detail.get("경도"))
            geo = {c: geo.get(c) for c in GEO_COLUMNS}
            # 공유 캡처 입력에 쓸 사진 현재값은 잠금 안에서 읽는다(DB 에는 쓰지 않음).
            photo_current = {c: existing.get(c) for c in PHOTO_COLUMNS}
            derived_for_capture = {**geo, **photo_current}
            try:
                capture_result = _community_capture_for(capture_rec, derived_for_capture, existing, trigger=trigger, engine=engine)
            except _cap.CaptureStoreUnavailable:
                raise
            except Exception as exc:
                raise _CaptureFailed("community_capture_failed") from exc
            now_ms = _now_ms()
            site = {c: _clean(rec.detail.get(c)) for c in DETAIL_SITE_COLUMNS}
            cls = _classification(rec, existing)
            raw_changed = _save_body(conn, rec, now_ms)
            _save_raw(conn, rec)

            if not _has_detail(existing):
                change = {"id": rec.id, "change_type": "신규"}
            elif (raw_changed or _classification_changed(existing, cls)
                    or any(comparable(existing.get(c)) != comparable(site[c]) for c in CHANGE_TRACKED_COLUMNS)):
                change = {"id": rec.id, "change_type": "변경"}
            else:
                change = None

            # 목록 열(_title_values)은 일부러 갱신하지 않는다 — 목록 크롤이 채운 값을 유지한다.
            # 사진 열(PHOTO_COLUMNS)은 갱신하지 않는다 — 사진 보충이 채우는 열이라 현재 값을 유지한다.
            row = {**site, **geo, **cls, "synced_at": now_ms if change else existing.get("synced_at")}
            conn.execute(update(REPORT).where(REPORT.c.ID == rec.id).values(**row))
            refresh_effective_rows(conn, [rec.id])
    except _ReparseSkipped:
        raise
    except Exception:
        if capture_result is not None:
            try:
                _cap.mark_personal_save(capture_result.event_id, False)
            except Exception as exc:
                note_fallback("reports_repo.reparse_mark_personal_save_failed", exc)
        raise
    try:
        if capture_result is not None:
            _cap.mark_personal_save(capture_result.event_id, True)
        _cap.remove_retry_id(rec.id)
    except Exception as exc:
        note_fallback("reports_repo.reparse_mark_personal_save_ok", exc)
    try:
        _write_raw_file_best_effort(rec)
    except Exception as exc:
        note_fallback("reports_repo.reparse_raw_file_unexpected", exc)
    if change is not None:
        change["category"] = cls["category"]
        if existing is not None and _has_detail(existing) and existing.get("category") != cls["category"]:
            change["previous_category"] = existing.get("category")
    return change


def save_crawled(engine, records, *, refresh_duplicates: bool = True, admission=None, capture_community=True) -> SaveResult:
    from services import community_capture as _cap

    result = SaveResult()
    consecutive_capture_failures = 0
    for rec in records:
        try:
            change = _save_one(engine, rec, admission=admission, capture_community=capture_community) if admission is not None or not capture_community else _save_one(engine, rec)
        except _CaptureFailed as exc:
            result.failed.append((rec.id, "community_capture_failed"))
            logger.LoggerFactory.logbot.error(f"ID {rec.id} 저장 실패: {exc}")
            consecutive_capture_failures += 1
            if consecutive_capture_failures >= 3:
                raise _cap.CaptureStoreUnavailable(
                    "community capture failed 3 times in a row; stopping crawl") from exc
            continue
        except _cap.CaptureStoreUnavailable:
            raise
        except Exception as exc:
            result.failed.append((rec.id, repr(exc)))
            logger.LoggerFactory.logbot.error(f"ID {rec.id} 저장 실패: {exc}")
            consecutive_capture_failures = 0
            continue
        consecutive_capture_failures = 0
        result.saved += 1
        if change:
            result.changed.append(change)
    if result.saved and refresh_duplicates:
        from core.database import database
        database._refresh_duplicate_groups(engine)
    if result.failed:
        logger.LoggerFactory.logbot.error(
            f"상세 저장 실패 {len(result.failed)}건: " + ", ".join(rid for rid, _ in result.failed[:20])
        )
    return result


# ── 화면용 표(effective) ─────────────────────────────────────────────────────

def _attachment_cutoff() -> str:
    return (datetime.now() - relativedelta(months=6)).strftime("%Y-%m-%d")


def effective_drift(conn) -> dict:
    """화면용 표가 신고 행과 어긋났는지 집계로 본다(개별 신고는 반환하지 않음). 어긋나지 않으면 빈 dict."""
    override = models.report_override_table
    source = REPORT.outerjoin(override, (override.c.ID == REPORT.c.ID) & (override.c.column_name == "처리기관코드"))
    # 명시적인 빈 값/NULL 수정도 원본보다 우선한다(refresh_effective_rows 와 같은 규칙).
    code = func.coalesce(case((override.c.ID.is_not(None), override.c.value), else_=REPORT.c.처리기관코드), "")
    merged_code = func.coalesce(EFFECTIVE.c.처리기관코드, "")
    expected = conn.execute(select(
        func.count().label("report_rows"),
        func.count().filter(code != "").label("report_codes"),
        func.count().filter(EFFECTIVE.c.ID.is_(None)).label("missing_ids"),
        func.count().filter(code != merged_code).label("code_mismatches"),
        func.count().filter(EFFECTIVE.c.category.is_distinct_from(REPORT.c.category)).label("category_mismatches"),
    ).select_from(source.outerjoin(EFFECTIVE, REPORT.c.ID == EFFECTIVE.c.ID)).where(REPORT.c.category.is_not(None))).mappings().one()
    actual = conn.execute(select(
        func.count().label("effective_rows"),
        func.count().filter(merged_code != "").label("effective_codes"),
    ).select_from(EFFECTIVE)).mappings().one()
    counts = {**expected, **actual}
    if (counts["report_rows"] != counts["effective_rows"]
            or counts["report_codes"] != counts["effective_codes"]
            or counts["missing_ids"] or counts["code_mismatches"] or counts["category_mismatches"]):
        return counts
    return {}


def refresh_effective_rows(conn, ids=None) -> None:
    """ids=None 이면 전체, 아니면 그 신고만 화면용 표를 다시 만든다(같은 규칙)."""
    ids = list(ids) if ids is not None else None
    conn.execute(delete(EFFECTIVE) if ids is None else delete(EFFECTIVE).where(EFFECTIVE.c.ID.in_(ids)))
    columns = list(models.REPORT_COLUMN_NAMES)
    stmt = select(*[REPORT.c[name] for name in columns]).where(REPORT.c.category.is_not(None))
    if ids is not None:
        stmt = stmt.where(REPORT.c.ID.in_(ids))
    conn.execute(EFFECTIVE.insert().from_select(columns, stmt))
    _apply_overrides(conn, ids)
    _apply_attachment_expiry(conn, ids)
    _apply_watch_flags(conn, ids)


def set_overrides(conn, record_id: str, provided: dict, site: dict) -> None:
    """사용자 편집값을 수정값 표에 쓴다(결정 D-1). 보낸 열만 다루고(S-2), 사이트 원본과 같아지면 수정값을 지운다(되돌리기).
    화면용 표의 "6개월 초과" 가림 글자는 수정값으로 받지 않는다(S-3)."""
    table = models.report_override_table
    now_ms = _now_ms()
    for column, value in provided.items():
        if column not in OVERRIDABLE_COLUMNS:
            continue
        if column in ATTACHMENT_COLUMNS and value == EXPIRED_ATTACHMENT:
            continue
        conn.execute(delete(table).where(table.c.ID == record_id).where(table.c.column_name == column))
        if comparable(value) != comparable(site.get(column)):
            conn.execute(table.insert().values(ID=record_id, column_name=column, value=value, updated_at=now_ms))


def _apply_overrides(conn, ids) -> None:
    table = models.report_override_table
    query = select(table.c.ID, table.c.column_name, table.c.value)
    if ids is not None:
        query = query.where(table.c.ID.in_(ids))
    for record_id, column, value in conn.execute(query):
        if column not in OVERRIDABLE_COLUMNS:
            logger.LoggerFactory.logbot.warning(f"[override] 허용되지 않은 열 무시: {record_id}.{column}")
            continue
        # 사용자 수정 주소는 원본 신고의 공식 좌표를 바꾸지 않는다.
        conn.execute(update(EFFECTIVE).where(EFFECTIVE.c.ID == record_id).values({column: value}))


def _apply_attachment_expiry(conn, ids) -> None:
    stmt = update(EFFECTIVE).where(EFFECTIVE.c.신고일 < _attachment_cutoff())
    if ids is not None:
        stmt = stmt.where(EFFECTIVE.c.ID.in_(ids))
    conn.execute(stmt.values(**{c: EXPIRED_ATTACHMENT for c in ATTACHMENT_COLUMNS}))


def _apply_watch_flags(conn, ids) -> None:
    watched = select(models.watchlist_table.c["신고번호"])
    for table in (REPORT, EFFECTIVE):
        stmt = update(table).values(감시목록=case((table.c.신고번호.in_(watched), "Y"), else_="N"))
        if ids is not None:
            stmt = stmt.where(table.c.ID.in_(ids))
        conn.execute(stmt)
