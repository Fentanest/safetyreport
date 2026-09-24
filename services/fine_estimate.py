"""금액 없는 과태료의 법정 최저 기준 추정 (docs/design/statistics-spec.md §4-1, §4-2).

- 추정값은 DB 에 저장하지 않고 입력(번호판, 신고 메뉴, 발생시각, 사진 촬영 시각)으로 매번 계산한다.
- 확정 과태료(`범칙금_과태료` 의 금액)와 섞지 않는다. 호출자는 별도 필드로만 내보낸다.
- 금액은 일반적으로 알려진 기본 금액(최저 기준)이다. 가중(고속도로, 감경 등)은 데이터로 알 수 없으면 반영하지 않는다.
- 모바일 Standalone 도 같은 규칙을 쓴다. 규칙을 바꾸면 RULE_VERSION 과 tests/fixtures/fine_estimate_vectors.json 을
  양쪽 레포에서 함께 바꾼다.
"""
from __future__ import annotations

import re
from datetime import datetime

RULE_VERSION = "2026-09-24.1"

# 차량 구분 → 과태료 표의 차종 구분 (도로교통법 시행령 별표6 비고)
PASSENGER = "승용등"   # 승용자동차 + 4톤 이하 화물자동차
VAN = "승합등"         # 승합자동차 + 4톤 초과 화물자동차 + 특수자동차 + 건설기계
MOTORCYCLE = "이륜등"  # 이륜자동차 + 원동기장치자전거

_PLATE_RE = re.compile(r"^(?:[가-힣]{2,})?(\d{2,3})([가-힣])(\d{4})$")
_MOTORCYCLE_RE = re.compile(r"^(?:[가-힣]{2,})?\d?[가-힣]\d?\d{4}$")


def normalize_plate(plate) -> str:
    return re.sub(r"\s+", "", str(plate or ""))


def vehicle_kind(plate) -> str | None:
    """번호판 차종기호 → 승용/승합/화물/특수/긴급/이륜. 판별 불가면 None. 이륜은 형식 추정."""
    text = normalize_plate(plate)
    match = _PLATE_RE.match(text)
    if match:
        digits = match.group(1)
        number = int(digits)
        if len(digits) == 2:
            if 1 <= number <= 69:
                return "승용"
            if 70 <= number <= 79:
                return "승합"
            if 80 <= number <= 97:
                return "화물"
            if 98 <= number <= 99:
                return "특수"
            return None
        if 100 <= number <= 699:
            return "승용"
        if 700 <= number <= 799:
            return "승합"
        if 800 <= number <= 979:
            return "화물"
        if 980 <= number <= 997:
            return "특수"
        if 998 <= number <= 999:
            return "긴급"
        return None
    if _MOTORCYCLE_RE.match(text):
        return "이륜"
    return None


def fine_class(plate) -> str:
    """과태료 표 차종 구분. 화물은 4톤 기준을 몰라 낮은 쪽(승용등), 판별 불가·긴급도 승용등."""
    kind = vehicle_kind(plate)
    if kind in ("승합", "특수"):
        return VAN
    if kind == "이륜":
        return MOTORCYCLE
    return PASSENGER


# (rule_id, 근거, 차종별 금액(원), 2시간 이상 금액)
_RULES = {
    "bus_lane": ("도로교통법 시행령 별표6 제3호(일반도로 전용차로)", {VAN: 60000, PASSENGER: 50000, MOTORCYCLE: 40000}, None),
    "parking": ("도로교통법 시행령 별표6 제6호(주정차)", {VAN: 50000, PASSENGER: 40000}, {VAN: 60000, PASSENGER: 50000}),
    "hydrant": ("도로교통법 시행령 별표6 제6호의2 나목(소화전, 표지 미확인)", {VAN: 50000, PASSENGER: 40000}, {VAN: 60000, PASSENGER: 50000}),
    "school_zone": ("도로교통법 시행령 별표7 1(어린이보호구역 주정차, 08~20시)", {VAN: 130000, PASSENGER: 120000}, {VAN: 140000, PASSENGER: 130000}),
    "ev_charging": ("친환경자동차법 시행령 별표 제2호 가목(충전구역 주차)", {None: 100000}, None),
    "disabled_parking": ("장애인등편의법 제17조제4항(장애인전용주차구역)", {None: 100000}, None),
    "waste": ("폐기물관리법 시행령 별표8 1)가)(휴대 생활폐기물 투기)", {None: 50000}, None),
    "overnight_truck": ("화물자동차 운수사업법 시행규칙 별표3 제2호(밤샘주차, 개인 1.5톤 이하)", {None: 50000}, None),
}


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def _parse_time(value):
    text = _text(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y:%m:%d %H:%M:%S"):
        try:
            return datetime.strptime(text[:19] if "S" in fmt else text[:16], fmt)
        except ValueError:
            continue
    return None


def photo_span_minutes(first, last) -> float | None:
    start, end = _parse_time(first), _parse_time(last)
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 60


def is_overnight_window(first, last) -> bool:
    """맨 앞·맨 뒤 사진이 같은 날 0:00~4:00 안에서 촬영되고 간격이 1시간 이상 (창을 넘는 촬영은 해당 없음)."""
    start, end = _parse_time(first), _parse_time(last)
    if start is None or end is None or start.date() != end.date():
        return False
    in_window = all(t.hour < 4 or (t.hour == 4 and t.minute == 0 and t.second == 0) for t in (start, end))
    return in_window and (end - start).total_seconds() >= 3600


def _occur_hour(occur_time) -> int | None:
    match = re.match(r"^\s*(\d{1,2}):(\d{2})", _text(occur_time))
    return int(match.group(1)) if match else None


def classify(record: dict) -> str | None:
    """추정 규칙 id. 과태료 대상이 아니거나 판별 불가면 None."""
    category = _text(record.get("category"))
    entry = _text(record.get("entry_value"))
    name = _text(record.get("신고명"))
    law = _text(record.get("위반법규"))
    plate = record.get("차량번호")
    first, last = record.get("사진_첫촬영"), record.get("사진_끝촬영")

    if "불법주정차신고" in entry or category == "parking":
        if is_commercial_truck(plate) and is_overnight_window(first, last):
            return "overnight_truck"
        if "충전" in entry:
            return "ev_charging"
        if "장애인" in entry:
            return "disabled_parking"
        if "소화전" in entry:
            return "hydrant"
        if "어린이" in entry:
            hour = _occur_hour(record.get("발생시각"))
            return "school_zone" if hour is not None and 8 <= hour < 20 else "parking"
        return "parking"
    if "쓰레기, 폐기물" in entry:
        return "waste"
    if category == "traffic" and (("버스" in name and "차로" in name) or "제15조" in law):
        return "bus_lane"
    return None


def is_commercial_truck(plate) -> bool:
    """사업용 화물 = 화물 차종기호 + 자동차운수사업용 일반용 기호(바·사·아·자·배).

    근거: 자동차 등록번호판 등의 기준에 관한 고시(국토교통부고시 제2025-121호) 제5조제1항 도표 —
    화물자동차 비사업용 800-979 / 일반사업용 80-97, 용도기호 일반용 "바, 사, 아, 자, 배", 대여사업용 "허, 하, 호".
    (2026-09-24 Opus 가 법령정보센터 도표 이미지 원문으로 대조)
    """
    text = normalize_plate(plate)
    match = _PLATE_RE.match(text)
    return bool(match) and vehicle_kind(text) == "화물" and match.group(2) in COMMERCIAL_TRUCK_SYMBOLS


COMMERCIAL_TRUCK_SYMBOLS = frozenset("바사아자배")


def estimate(record: dict) -> dict | None:
    """금액 없는 과태료 행이면 {'amount', 'rule', 'basis'} 를, 아니면 None 을 돌려준다."""
    rule_id = classify(record)
    if rule_id is None:
        return None
    basis, amounts, surcharge = _RULES[rule_id]
    cls = fine_class(record.get("차량번호"))
    table = amounts
    span = photo_span_minutes(record.get("사진_첫촬영"), record.get("사진_끝촬영"))
    if surcharge and span is not None and span >= 120:
        table = surcharge
        basis += " · 2시간 이상"
    amount = table.get(cls, table.get(None))
    if amount is None:
        return None
    return {"amount": int(amount), "rule": rule_id, "basis": f"{basis} · {cls if None not in table else '차종 무관'}"}
