"""주정차 신고 사진의 촬영 시각 수집 (docs/plans/crawler-photo-capture-time-plan.md).

안전신문고 앱 카메라 사진은 EXIF DateTimeOriginal 에 촬영 시각을 남긴다(사진 위 "촬영일시" 표시와 같다).
첨부 URL 은 약 6개월 뒤 만료되므로 크롤링 시점에 앞부분만 받아 읽는다. 표준 라이브러리만 쓴다(제품 의존성 추가 없음).
"""
from __future__ import annotations

import struct
from datetime import datetime

import requests

from core.utils.runtime_mode import block_if_fixture

PHOTO_COLUMNS = ("사진_첫촬영", "사진_끝촬영", "사진_촬영수")
MAX_BYTES = 128 * 1024
_TIMEOUT_SECONDS = 15
_TAG_DATETIME = 0x0132
_TAG_EXIF_IFD = 0x8769
_TAG_DATETIME_ORIGINAL = 0x9003


def _read_ascii_tag(tiff: bytes, endian: str, ifd_offset: int, wanted: int) -> str | None:
    if ifd_offset + 2 > len(tiff):
        return None
    (count,) = struct.unpack_from(endian + "H", tiff, ifd_offset)
    for index in range(count):
        entry = ifd_offset + 2 + index * 12
        if entry + 12 > len(tiff):
            return None
        tag, typ, n, value = struct.unpack_from(endian + "HHII", tiff, entry)
        if tag != wanted:
            continue
        if tag == _TAG_EXIF_IFD:
            return str(value)
        if typ != 2 or n == 0:
            return None
        start = entry + 8 if n <= 4 else value
        raw = tiff[start:start + n]
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip() or None
    return None


def parse_exif_datetime(data: bytes) -> str | None:
    """JPEG 앞부분에서 촬영 시각을 'YYYY-MM-DD HH:MM:SS' 로. 없거나 잘렸으면 None."""
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    pos = 2
    while pos + 4 <= len(data):
        if data[pos] != 0xFF:
            return None
        marker = data[pos + 1]
        if marker in (0xD9, 0xDA):  # EOI / SOS: 메타데이터 구간 끝
            return None
        (length,) = struct.unpack_from(">H", data, pos + 2)
        segment = data[pos + 4:pos + 2 + length]
        if marker == 0xE1 and segment[:6] == b"Exif\x00\x00":
            return _parse_tiff_datetime(segment[6:])
        pos += 2 + length
    return None


def _parse_tiff_datetime(tiff: bytes) -> str | None:
    if len(tiff) < 8 or tiff[:2] not in (b"II", b"MM"):
        return None
    endian = "<" if tiff[:2] == b"II" else ">"
    (ifd0,) = struct.unpack_from(endian + "I", tiff, 4)
    value = None
    exif_ifd = _read_ascii_tag(tiff, endian, ifd0, _TAG_EXIF_IFD)
    if exif_ifd is not None:
        value = _read_ascii_tag(tiff, endian, int(exif_ifd), _TAG_DATETIME_ORIGINAL)
    if value is None:
        value = _read_ascii_tag(tiff, endian, ifd0, _TAG_DATETIME)
    if not value:
        return None
    try:
        return datetime.strptime(value[:19], "%Y:%m:%d %H:%M:%S").strftime("%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def fetch_capture_time(url: str, session=None) -> str | None:
    """사진 앞부분만 받아 촬영 시각을 읽는다. 원본 서버는 Range 를 지원하지 않아 스트리밍 후 끊는다."""
    block_if_fixture("photo capture fetch")
    http = session or requests
    with http.get(url, stream=True, timeout=_TIMEOUT_SECONDS, headers={"User-Agent": "Mozilla/5.0"}) as response:
        response.raise_for_status()
        chunks = bytearray()
        for chunk in response.iter_content(chunk_size=16 * 1024):
            chunks.extend(chunk)
            if len(chunks) >= MAX_BYTES:
                break
    return parse_exif_datetime(bytes(chunks))


def photo_urls(attached_photos) -> list[str]:
    return [line.strip() for line in str(attached_photos or "").split("\n") if line.strip().startswith("http")]


def collect(attached_photos, fetch=None) -> dict | None:
    """첨부 사진들의 첫/끝 촬영 시각과 읽은 장수. URL 이 없으면 None(시도 안 함).

    네트워크 오류가 하나라도 나면 None 을 돌려 다음 크롤링에서 다시 시도하게 한다.
    모두 받았지만 촬영 시각이 없으면 사진_촬영수 = 0 (다시 시도하지 않음).
    """
    urls = photo_urls(attached_photos)
    if not urls:
        return None
    fetch = fetch or fetch_capture_time
    times = []
    for url in urls:
        try:
            value = fetch(url)
        except Exception:
            return None
        if value:
            times.append(value)
    times.sort()
    return {
        "사진_첫촬영": times[0] if times else None,
        "사진_끝촬영": times[-1] if times else None,
        "사진_촬영수": len(times),
    }


def is_parking_report(category, entry_value) -> bool:
    return str(category or "") == "parking" or "불법주정차신고" in str(entry_value or "")


def backfill_missing(engine, *, limit: int = 30) -> int:
    """촬영 시각을 아직 못 읽은 주정차 신고를 다시 시도한다(S-8). 반환: 채운 건수.

    종결된 신고는 다시 크롤링되지 않으므로 한 번 실패하면 영영 비어 있었다. 첨부 URL 은 약 6개월 뒤 만료되므로
    신고일이 6개월 이내인 것만, 크롤링 한 번에 [limit] 건까지. 네트워크는 트랜잭션 밖에서 한다.
    """
    from sqlalchemy import select, update

    from core.database import models
    from core.storage import reports_repo

    detail, title = models.detail_parking_table, models.title_table
    with engine.connect() as conn:
        rows = conn.execute(
            select(detail.c.ID, detail.c["첨부사진"])
            .select_from(detail.join(title, title.c.ID == detail.c.ID))
            .where(
                detail.c["사진_촬영수"].is_(None),
                detail.c["첨부사진"].like("http%"),
                title.c["신고일"] >= reports_repo._attachment_cutoff(),
            )
            .order_by(detail.c.ID.desc())
            .limit(limit)
        ).all()
    filled = 0
    for record_id, photos in rows:
        collected = collect(photos)  # 네트워크 오류는 None → 다음 크롤링에서 다시
        if collected is None:
            continue
        with engine.begin() as conn:
            conn.execute(update(detail).where(detail.c.ID == record_id, detail.c["사진_촬영수"].is_(None)).values(**collected))
            reports_repo.refresh_merge_rows(conn, [record_id])
        filled += 1
    return filled
