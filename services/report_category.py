"""신고 분류: 안전신문고 SSNPC 코드(SSNPC_CD+SSNPC_TYPE) → 5분류(traffic·parking·safety·living·other).

정본은 `contracts/report-categories.json`(두 레포 바이트 동일). 이 모듈의 표는 그 파일과 같아야 하고
`tests/test_report_category.py` 가 codes·menus·aliases·vectors 를 모두 대조한다. 모바일 `lib/services/report_category.dart` 도 같다.
규칙: 코드가 있으면 코드로만 정한다. 코드가 없을 때만 entry_value('메뉴-소분류')로 찾는다.
"""
from __future__ import annotations

import re
from typing import NamedTuple

CATEGORIES = ("traffic", "parking", "safety", "living", "other")
CATEGORY_LABELS = {"traffic": "교통위반", "parking": "주정차위반", "safety": "안전신고", "living": "생활불편", "other": "기타"}

GROUP_CATEGORY = {"TV": "traffic", "IP": "parking", "EP": "parking", "CM": "safety", "DR": "living"}

# (cd, type) → (이름, category). (구) 코드 셋은 지금의 후계 분류로 보낸다(CM-05 → 생활불편, CM-06·CM-08 → 교통위반).
CODES: dict[tuple[str, str], tuple[str, str]] = {
    ("CM", "01"): ("도로, 시설물 파손 및 고장", "safety"),
    ("CM", "02"): ("사업장 안전(건설·공사현장 등)", "safety"),
    ("CM", "03"): ("(구)지하수 미등록 시설·방치공", "safety"),
    ("CM", "04"): ("대기·수질오염", "safety"),
    ("CM", "05"): ("(구)쓰레기, 폐기물, 유독물", "living"),
    ("CM", "06"): ("(구)교통위반", "traffic"),
    ("CM", "07"): ("기타 안전·환경 위험요소", "safety"),
    ("CM", "08"): ("(구)자동차 안전신고", "traffic"),
    ("CM", "09"): ("대기오염", "safety"),
    ("CM", "10"): ("수질오염", "safety"),
    ("CM", "11"): ("소방안전", "safety"),
    ("CM", "12"): ("(구)감염병(코로나19)", "safety"),
    ("CM", "13"): ("계절별 집중신고", "safety"),
    ("CM", "14"): ("하천·계곡 내 불법시설", "safety"),
    ("CM", "15"): ("고층 건축물 화재 안전", "safety"),
    ("IP", "01"): ("소화전", "parking"),
    ("IP", "02"): ("교차로 모퉁이", "parking"),
    ("IP", "03"): ("버스정류소", "parking"),
    ("IP", "04"): ("횡단보도", "parking"),
    ("IP", "05"): ("어린이보호구역", "parking"),
    ("IP", "06"): ("인도", "parking"),
    ("EP", "01"): ("장애인전용구역", "parking"),
    ("EP", "02"): ("기타불법주정차", "parking"),
    ("EP", "03"): ("소방차전용구역", "parking"),
    ("EP", "04"): ("친환경차충전구역", "parking"),
    ("TV", "01"): ("자동차 안전신고", "traffic"),
    ("TV", "02"): ("교통위반(고속도로 포함)", "traffic"),
    ("TV", "03"): ("이륜차 위반", "traffic"),
    ("TV", "04"): ("(구)적재물 추락방지,중량·용량 위반", "traffic"),
    ("TV", "05"): ("버스전용차로 위반(고속도로제외)", "traffic"),
    ("TV", "06"): ("번호판 규정 위반", "traffic"),
    ("TV", "07"): ("불법등화, 반사판(지) 가림·손상", "traffic"),
    ("TV", "08"): ("불법 튜닝, 해체, 조작", "traffic"),
    ("TV", "09"): ("기타 자동차 안전기준 위반", "traffic"),
    ("TV", "10"): ("난폭/보복운전", "traffic"),
    ("DR", "01"): ("불법광고물", "living"),
    ("DR", "02"): ("자전거·이륜차 방치 및 불편", "living"),
    ("DR", "03"): ("(구)청소년 유해업소", "living"),
    ("DR", "04"): ("(구)에너지 과소비", "living"),
    ("DR", "05"): ("기타 생활불편", "living"),
    ("DR", "06"): ("쓰레기, 폐기물", "living"),
    ("DR", "07"): ("해양 쓰레기", "living"),
    ("DR", "08"): ("불법 숙박", "living"),
}

# 정규화한 메뉴 이름 → (대분류 후보, category)
ENTRY_MENUS: dict[str, tuple[tuple[str, ...], str]] = {
    "불법주정차신고": (("IP", "EP"), "parking"),
    "자동차·교통위반": (("TV",), "traffic"),
    "자동차·교통위반신고": (("TV",), "traffic"),
    "안전신고": (("CM",), "safety"),
    "생활불편신고": (("DR",), "living"),
}

# 이름이 바뀌어 정규화로는 못 찾는 옛 소분류 (대분류 후보, 정규화 소분류) → 코드
ENTRY_ALIASES: dict[tuple[tuple[str, ...], str], tuple[str, str]] = {
    (("TV",), "버스전용차로위반(일반도로)"): ("TV", "05"),
    (("CM",), "쓰레기,폐기물"): ("CM", "05"),
}

_SPACE = re.compile(r"\s+")


class Resolved(NamedTuple):
    ssnpc_cd: str | None    # 확정한 코드(없으면 None). 응답에 코드가 있으면 대분류를 몰라도 원래 값을 정규화해 둔다
    ssnpc_type: str | None
    code: str | None        # 'IP-05' 처럼 코드표에 있는 코드만. 없으면 None
    category: str


def _norm(text) -> str:
    return _SPACE.sub("", str(text or ""))


def _norm_name(name: str) -> str:
    return _norm(name).removeprefix("(구)")


def _norm_sub(sub: str) -> str:
    return _norm(sub).removeprefix("(구)").removesuffix("불법주차")


def normalize_code(ssnpc_cd, ssnpc_type) -> tuple[str | None, str | None]:
    """응답 값 정규화: 대분류는 대문자, 소분류는 두 자리 숫자. 비어 있으면 None."""
    cd = _norm(ssnpc_cd).upper() or None
    raw_type = _norm(ssnpc_type)
    type_ = (raw_type.zfill(2) if raw_type.isdigit() else raw_type) or None
    return cd, type_


_NAME_INDEX: dict[tuple[str, str], tuple[str, str]] = {
    (cd, _norm_name(name)): (cd, type_) for (cd, type_), (name, _cat) in CODES.items()
}


def _from_entry_value(entry_value) -> tuple[str | None, str | None, str]:
    text = str(entry_value or "").strip()
    if not text:
        return None, None, "other"
    menu, _, sub = text.partition("-")
    found = ENTRY_MENUS.get(_norm(menu))
    if found is None:
        # '-' 가 아닌 구분자 등: 문구가 아는 메뉴 이름으로 시작하면 그중 가장 긴 메뉴(소분류는 모름)
        whole = _norm(text)
        prefixes = [name for name in ENTRY_MENUS if whole.startswith(name)]
        if not prefixes:
            return None, None, "other"
        return None, None, ENTRY_MENUS[max(prefixes, key=len)][1]
    cds, category = found
    key = _norm_sub(sub)
    if key:
        alias = ENTRY_ALIASES.get((cds, key))
        if alias:
            return alias[0], alias[1], CODES[alias][1]
        for cd in cds:
            hit = _NAME_INDEX.get((cd, key))
            if hit:
                return hit[0], hit[1], CODES[hit][1]
    return None, None, category


def resolve(ssnpc_cd=None, ssnpc_type=None, entry_value=None) -> Resolved:
    cd, type_ = normalize_code(ssnpc_cd, ssnpc_type)
    if cd:
        known = CODES.get((cd, type_ or ""))
        if known:
            return Resolved(cd, type_, f"{cd}-{type_}", known[1])
        if type_ is None:
            # 대분류만 오고 소분류가 빠졌으면 접수 메뉴의 소분류를 같은 대분류 안에서만 쓴다(최종 감사 #3).
            found_cd, found_type, category = _from_entry_value(entry_value)
            if found_cd == cd:
                return Resolved(found_cd, found_type, f"{found_cd}-{found_type}", category)
        return Resolved(cd, type_, None, GROUP_CATEGORY.get(cd, "other"))
    found_cd, found_type, category = _from_entry_value(entry_value)
    if found_cd:
        return Resolved(found_cd, found_type, f"{found_cd}-{found_type}", category)
    return Resolved(None, None, None, category)


def category_of(ssnpc_cd=None, ssnpc_type=None, entry_value=None) -> str:
    return resolve(ssnpc_cd, ssnpc_type, entry_value).category


def code_name(code: str | None) -> str:
    """'IP-05' → '어린이보호구역'. 모르면 ''."""
    if not code or "-" not in code:
        return ""
    cd, _, type_ = code.partition("-")
    known = CODES.get((cd, type_))
    return known[0] if known else ""


#: 5분류 이전 화면·통계·공유 계약이 쓰던 3분류. safety·living 은 예전 '기타'에 들어 있었다.
LEGACY_CATEGORIES = ("traffic", "parking", "other")


def legacy_category(category) -> str:
    """5분류 → 예전 3분류(traffic·parking·other). 구앱 API 호환과 3분류 화면이 쓴다."""
    return category if category in ("traffic", "parking") else "other"


def expand_legacy(category) -> tuple[str, ...]:
    """예전 3분류 값 → 그에 해당하는 5분류 묶음. 'other' 는 safety·living·other."""
    if category == "other":
        return ("safety", "living", "other")
    return (category,)
