"""금액 없는 과태료의 법정 최저 기준 추정 (docs/design/statistics-spec.md §4-1, §4-2).

- 추정값은 DB 에 저장하지 않고 입력(번호판, 신고 메뉴, 발생시각, 사진 촬영 시각)으로 매번 계산한다.
- 확정 과태료(`범칙금_과태료` 의 금액)와 섞지 않는다. 호출자는 별도 필드로만 내보낸다.
- 금액은 일반적으로 알려진 기본 금액(최저 기준)이다. 가중(고속도로, 감경 등)은 데이터로 알 수 없으면 반영하지 않는다.
- 모바일 Standalone 도 같은 규칙을 쓴다. 규칙을 바꾸면 RULE_VERSION 과 tests/fixtures/fine_estimate_vectors.json 을
  양쪽 레포에서 함께 바꾼다.
- 번호판 입력은 services/plate_normalize.normalize_plate 로 먼저 정규화한다(세 레포 공통 규칙, 2026-10-12
  사용자·Opus 결정). 이 전처리는 "같은 번호판을 가리키는 입력을 더 많이 알아보게" 할 뿐 차종 판정표(숫자
  구간·사업용 기호)는 그대로이므로 RULE_VERSION 을 올리지 않는다 — 기존 fine_estimate_vectors.json 의 깨끗한
  번호판 60건은 전부 같은 결과를 낸다(tests/test_fine_estimate.py 로 확인).
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from services import plate_normalize, report_category
from services.law_values import primary_law

RULE_VERSION = "2026-10-13.2"

_TRAFFIC_FINE_TABLE = json.loads(
    (Path(__file__).parent / "data/traffic_fine_table.json").read_text(encoding="utf-8")
)["articles"]


def normalize_violation_article(value) -> str | None:
    """단일 도로교통법 조항만 정규화. 다른 법/복수 조항/모르는 하위 조항은 추측하지 않는다."""
    text = re.sub(r"\s+", "", str(value or ""))
    match = re.fullmatch(
        r"(?:도로교통법)?제([0-9]+)조(의[0-9]+)?(?:제?([0-9]+)항)?(?:제?([0-9]+)호(의[0-9]+)?)?", text
    )
    if not match:
        return None
    article, subarticle, paragraph, item, subitem = match.groups()
    return (f"도로교통법 제{article}조" + (subarticle or "")
            + (f"제{paragraph}항" if paragraph else "")
            + (f"제{item}호" if item else "") + (subitem or ""))

# 차량 구분 → 과태료 표의 차종 구분 (도로교통법 시행령 별표6 비고)
PASSENGER = "승용등"   # 승용자동차 + 4톤 이하 화물자동차
VAN = "승합등"         # 승합자동차 + 4톤 초과 화물자동차 + 특수자동차 + 건설기계
MOTORCYCLE = "이륜등"  # 이륜자동차 + 원동기장치자전거

_PLATE_RE = re.compile(r"^(?:[가-힣]{2,})?(\d{2,3})([가-힣])(\d{4})$")
_MOTORCYCLE_RE = re.compile(r"^(?:[가-힣]{2,})?\d?[가-힣]\d?\d{4}$")


def normalize_plate(plate) -> str:
    """공백만 지운 원시 문자열(레거시 호환용). 차종 판정은 plate_normalize.normalize_plate 를 쓴다."""
    return re.sub(r"\s+", "", str(plate or ""))


def vehicle_kind(plate) -> str | None:
    """번호판 차종기호 → 승용/승합/화물/특수/긴급/이륜. 판별 불가면 None. 이륜은 형식 추정.

    plate_normalize.normalize_plate 로 먼저 정규화한다(군더더기·설명 문구·보이지 않는 문자 제거,
    서로 다른 후보 2개 이상이거나 미인식이면 None) — 2026-10-12 결정, 원문은 호출자가 그대로 보존한다.
    """
    text = plate_normalize.normalize_plate(plate)
    if text is None:
        return None
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
    "fire_lane": ("소방기본법 시행령 별표3 제2호 아목(소방차 전용구역 주차·진입 방해, 1차)", {None: 500000}, None),
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


def _no_subcategory_info(resolved: report_category.Resolved, entry_value) -> bool:
    """진짜 '소분류 없음'(메뉴만 있고 세부가 없음)일 때만 True.

    소분류 코드(SSNPC_TYPE)가 있으면(모르는 코드라도) 소분류가 있는데 못 알아본 것이므로 False —
    EP-99 처럼 코드표에 없는 코드는 밤샘주차가 아니라 일반 주정차로 떨어져야 한다(Astra 검토 항목 7).
    대분류(SSNPC_CD)만 있고 소분류가 비면 소분류 없음이다(Astra 재검수 검토 항목 6). 그때는 entry_value 의
    '메뉴-소분류'에서 '-' 뒤가 비어 있는지로 판단한다.
    """
    if resolved.ssnpc_type:
        return False
    if resolved.ssnpc_cd not in (None, "EP"):
        # 대분류가 IP(소화전·교차로 등 지정 구역)면 '기타 불법주정차'가 아니다 — 밤샘주차는 EP(일반)만(최종 감사 항목 3).
        return False
    _menu, _sep, sub = str(entry_value or "").strip().partition("-")
    return not sub.strip()


def classify(record: dict) -> str | None:
    """추정 규칙 id. 과태료 대상이 아니거나 판별 불가면 None."""
    category = _text(record.get("category"))
    entry_value = record.get("entry_value")
    resolved = report_category.resolve(record.get("ssnpc_cd"), record.get("ssnpc_type"), entry_value)
    code = resolved.code
    name = _text(record.get("신고명"))
    law = _text(primary_law(record.get("위반법규")))
    plate = record.get("차량번호")
    first, last = record.get("사진_첫촬영"), record.get("사진_끝촬영")

    if resolved.category == "parking" or category == "parking":
        if code == "EP-02" or (code is None and _no_subcategory_info(resolved, entry_value)):
            if is_commercial_truck(plate) and is_overnight_window(first, last):
                return "overnight_truck"
            return "parking"
        if code == "EP-04":
            return "ev_charging"
        if code == "EP-01":
            return "disabled_parking"
        if code == "EP-03":
            return "fire_lane"
        if code == "IP-01":
            return "hydrant"
        if code == "IP-05":
            hour = _occur_hour(record.get("발생시각"))
            return "school_zone" if hour is not None and 8 <= hour < 20 else "parking"
        return "parking"
    if code in ("DR-06", "CM-05"):
        return "waste"
    if (resolved.category == "traffic" or category == "traffic") and (code == "TV-05" or ("버스" in name and "차로" in name) or "제15조" in law):
        return "bus_lane"
    return None


def is_commercial_truck(plate) -> bool:
    """사업용 화물 = 화물 차종기호 + 자동차운수사업용 일반용 기호(바·사·아·자·배).

    근거: 자동차 등록번호판 등의 기준에 관한 고시(국토교통부고시 제2025-121호) 제5조제1항 도표 —
    화물자동차 비사업용 800-979 / 일반사업용 80-97, 용도기호 일반용 "바, 사, 아, 자, 배", 대여사업용 "허, 하, 호".
    (2026-09-24 Opus 가 법령정보센터 도표 이미지 원문으로 대조)
    """
    text = plate_normalize.normalize_plate(plate)
    if text is None:
        return False
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


# ── 통계·공유 payload 공통 판정 (fine-estimate-contract.md) ──────────────────────
# 로컬 통계가 '추정 과태료'로 세는 신고 = 과태료 처분인데 금액을 모름(아래 두 함수) + estimate() 가 None 이 아님.
# 이 모듈이 정본이고 services/stats/metrics.py·services/community_capture.py 는 이 함수들을 그대로 쓴다
# (판정을 각자 다시 구현하지 않는다).

def legal_fine_amounts(record: dict | None) -> set[int]:
    """기존 추정 규칙 금액 ∪ 확인된 위반 조항·차종의 모든 법정 금액(보정 전용)."""
    record = record or {}
    rule = _RULES.get(classify(record))
    cls = fine_class(record.get("차량번호"))
    result = set()
    if rule is not None:
        _, amounts, surcharge = rule
        result.update(table.get(cls, table.get(None)) for table in (amounts, surcharge or {})
                      if table.get(cls, table.get(None)) is not None)
    entry = _TRAFFIC_FINE_TABLE.get(normalize_violation_article(primary_law(record.get("위반법규"))))
    if entry:
        amounts = entry["amounts"]
        kind = vehicle_kind(record.get("차량번호"))
        # 화물의 4톤 경계/긴급의 원차종은 번호판만으로 알 수 없다.
        classes = ((PASSENGER, VAN) if kind in ("화물", "긴급") else
                   tuple(amounts) if kind is None else (cls,))
        for key in (*classes, "차종무관"):
            result.update(amounts.get(key, ()))
    return result


# 숫자 토큰은 앞/뒤 구분자까지 포함한다. 부분 숫자만 골라 잘못된 형식을 정상으로 읽지 않는다.
AMOUNT_TOKEN_PATTERN = r"[0-9,.]+"
NORMAL_AMOUNT_PATTERN = r"[0-9]+|[0-9]{1,3}(?:[,.][0-9]{3})+"
_AMOUNT_TOKEN_RE = re.compile(rf"({AMOUNT_TOKEN_PATTERN})\s*원")


def parse_amount_number(number: str, legal_amounts=()) -> int | None:
    """정상 숫자는 그대로. 깨진 묶음은 원래 숫자/묶음 끝 0 보정 후보를 법정 금액과 대조한다."""
    if re.fullmatch(NORMAL_AMOUNT_PATTERN, number):
        return int(re.sub(r"[,.]", "", number))
    if not re.fullmatch(r"[0-9]+(?:[,.][0-9]+)+", number):
        return None
    parts = re.split(r"[,.]", number)
    candidates = {int("".join(parts))}
    normalized = [parts[0]]
    if len(parts[0]) <= 3:
        for part in parts[1:]:
            if len(part) < 3:
                part = part.ljust(3, "0")
            elif len(part) > 3:
                if set(part[3:]) != {"0"}:
                    break
                part = part[:3]
            normalized.append(part)
        else:
            candidates.add(int("".join(normalized)))
    matches = candidates.intersection(legal_amounts)
    return matches.pop() if len(matches) == 1 else None


def corrected_fine_text(text, record: dict | None = None) -> str | None:
    """보정된 원문만 표시용 문자열 반환. DB/호출자의 원문은 수정하지 않는다."""
    text = str(text or "")
    if "과태료" not in text:
        return None
    match = _AMOUNT_TOKEN_RE.search(text)
    if not match or re.fullmatch(NORMAL_AMOUNT_PATTERN, match[1]):
        return None
    amount = parse_amount_number(match[1], legal_fine_amounts(record))
    if amount is None:
        return None
    return text[:match.start(1)] + f"{amount:,}" + text[match.end(1):]


def extract_fine_amount(text, record: dict | None = None) -> int:
    """`범칙금_과태료` 원문에서 과태료 확정 금액을 읽는다. 과태료가 아니거나 못 읽으면 0."""
    if not text:
        return 0
    text = str(text)
    if "과태료" not in text:
        return 0
    # '40.000원' 처럼 점을 천 단위 구분자로 쓴 답변도 있다. 모바일 `extractFineAmount` 와 같은 규칙.
    match = _AMOUNT_TOKEN_RE.search(text)
    if match:
        return parse_amount_number(match.group(1), () if re.fullmatch(NORMAL_AMOUNT_PATTERN, match.group(1)) else legal_fine_amounts(record)) or 0
    return 0


def is_fine_amount_unknown(text, record: dict | None = None) -> bool:
    """과태료 처분인데 금액을 읽을 수 없는 경우(0원과 구분, statistics-spec S-05)."""
    return "과태료" in str(text or "") and extract_fine_amount(text, record) == 0


# estimate() 입력과 같은 열만(로컬 통계·공유 payload 공통, 분류 코드를 포함해 로컬 통계·공유 payload가 같은 유형을 판정한다).
FINE_ESTIMATE_FIELDS = ("category", "entry_value", "신고명", "위반법규", "차량번호", "사진_첫촬영", "사진_끝촬영", "발생시각", "ssnpc_cd", "ssnpc_type")


def estimate_if_unknown(fine_amount_text, record: dict) -> dict | None:
    """금액 없는 과태료 신고만 추정한다. `fine_amount_text` = `범칙금_과태료` 원문, `record` 키는 FINE_ESTIMATE_FIELDS.
    확정 금액이 있거나(과태료가 아니거나 금액이 읽히면) 규칙이 못 알아보면 None."""
    if not is_fine_amount_unknown(fine_amount_text, record):
        return None
    return estimate(record)
