"""상세 원본 정규 직렬화(2026-10-17 명세 1-1).

응답 envelope(JSON 객체) 전체를 저장한다. `result` 가 객체일 때만 그 안의
신고자 키 5개(`C_NAME`, `C_EMAIL`, `C_PHONE`, `C_PHONE2`, `C_ID`)를 지운다.
다른 위치의 같은 이름은 건드리지 않는다. 키 순서는 응답 순서를 유지한다.

직렬화: UTF-8, 비ASCII 그대로, 공백 없는 구분자
(`json.dumps(obj, ensure_ascii=False, separators=(",", ":"))`).
`raw_sha256` 은 그 바이트의 sha256(hex 소문자).
DB 저장값은 그 바이트를 zlib(RFC 1950, 레벨 6)으로 압축한 BLOB 이다.
읽을 때는 해제 뒤 sha256 이 맞는지 확인한다. 안 맞으면 그 행은 "손상"으로
보고 원본 보기·재파싱에서 쓰지 않는다(조용히 버리지 않고 로그를 남긴다).

서버와 모바일의 압축 바이트가 달라도 된다. 교환·백업은 저장된 BLOB
바이트를 그대로 옮긴다(재압축 금지).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import zlib
from datetime import datetime, timezone

from core.utils.fallback import note_fallback

#: `result` 객체에서만 지우는 신고자 키 5개. 순서 고정(계약 벡터와 같은 순서).
REPORTER_KEYS = ("C_NAME", "C_EMAIL", "C_PHONE", "C_PHONE2", "C_ID")

#: 원본 파일 이름에 쓸 수 있는 신고 ID. 실제 ID 는 안전신문고 C_NO 숫자 문자열이다.
#: 파일 경로에 그대로 넣어도 디렉터리를 벗어날 수 없는 문자(영숫자·`_`·`-`)만 허용하고,
#: 슬래시·역슬래시·점·공백·빈 값 등 그 밖은 쓰지 않고 진단만 기록한다.
_REPORT_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")

_RAW_DIRNAME = "raw_details"

_FETCHED_AT_FORMAT = "%Y-%m-%dT%H:%M:%S.%f"  # 밀리초 3자리 + Z (아래에서 자름)


def sanitize_envelope(envelope: dict) -> dict:
    """신고자 키 5개를 뺀 사본. 입력은 바꾸지 않는다."""
    cleaned = dict(envelope)
    result = cleaned.get("result")
    if isinstance(result, dict):
        rest = {k: v for k, v in result.items() if k not in REPORTER_KEYS}
        cleaned["result"] = rest
    return cleaned


def canonical_bytes(envelope: dict) -> bytes:
    """정규 직렬화 바이트(신고자 키 제거 → 공백 없는 JSON → UTF-8)."""
    return json.dumps(sanitize_envelope(envelope), ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def sha256_hex(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def compress(payload: bytes) -> bytes:
    return zlib.compress(payload, 6)


def decompress(payload: bytes) -> bytes:
    return zlib.decompress(payload)


def verify(blob: bytes, expected_sha256: str) -> bytes:
    """zlib 해제 + sha256 대조. 안 맞으면 ValueError(호출자가 "손상"으로 기록)."""
    try:
        payload = zlib.decompress(blob)
    except zlib.error as exc:
        raise ValueError(f"상세 원본 zlib 해제 실패: {exc}") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected_sha256:
        raise ValueError("상세 원본 sha256 불일치(손상)")
    return payload


def is_safe_report_id(report_id) -> bool:
    """원본 파일 이름에 그대로 넣어도 되는 신고 ID 인지. 아니면 False."""
    return isinstance(report_id, str) and _REPORT_ID_RE.fullmatch(report_id) is not None


def raw_file_path(report_id: str, directory: str) -> str | None:
    """`directory/<ID>.json` 정규화 경로. ID 가 파일 이름 규칙에 안 맞거나 정규화한 최종 경로가
    `directory` 를 벗어나면 None(쓰지 않고 호출자가 진단만 기록한다)."""
    if not is_safe_report_id(report_id):
        return None
    base = os.path.realpath(directory)
    path = os.path.realpath(os.path.join(base, f"{report_id}.json"))
    if os.path.dirname(path) != base:
        return None
    return path


def utc_now_fetched_at() -> str:
    """UTC ISO8601, 밀리초, `Z`(예: `2026-10-17T12:34:56.789Z`)."""
    now = datetime.now(timezone.utc)
    return now.strftime(_FETCHED_AT_FORMAT)[:-3] + "Z"


def write_raw_file(report_id: str, sanitized: dict) -> str | None:
    """`<datapath>/raw_details/<ID>.json` 에 들여쓰기 2칸 JSON 쓰기(임시 파일→rename).

    ID 가 파일 이름 규칙에 안 맞거나 최종 경로가 `raw_details` 를 벗어나면 쓰지 않고
    대체 진단 기록만 남기고 None 을 돌려준다. 폴더 생성 실패를 포함한 모든 파일 쓰기 실패는
    예외를 밖으로 내지 않고 진단 기록만 남기고 None 을 돌려준다(DB 저장을 막지 않는다).
    """
    import settings.settings as settings

    directory = os.path.join(settings.datapath, _RAW_DIRNAME)
    path = raw_file_path(report_id, directory)
    if path is None:
        note_fallback("raw_detail.write_file_invalid_id", ValueError("unsafe report id"))
        return None
    tmp: str | None = None
    try:
        os.makedirs(directory, exist_ok=True)
        payload = json.dumps(sanitized, ensure_ascii=False, indent=2).encode("utf-8")
        tmp = path + f".tmp-{os.getpid()}"
        with open(tmp, "wb") as fh:
            fh.write(payload)
        os.replace(tmp, path)
        return path
    except Exception as exc:  # 파일 쓰기 실패는 DB 저장을 막지 않는다
        note_fallback("raw_detail.write_file", exc)
        try:
            if tmp is not None and os.path.exists(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return None
