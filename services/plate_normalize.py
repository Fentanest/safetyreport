"""세 레포(safetyreport, safetyreport-mobile, safetyreport-community-map) 공통 번호판 정규화.

**원문은 절대 바꾸지 않는다.** 이 모듈은 DB·크롤러 저장값(`차량번호`, 지도 `vehicle_raw`)을 그대로 두고
"읽을 때"(통계, 중복차량, 추정 과태료 차종 판정 등) 번호판 토큰만 뽑아내는 순수 함수다.
세 레포가 같은 규칙을 쓴다 — 하나를 바꾸면 세 레포의 공유 벡터 파일(바이트 동일)과 이 문서를 함께 바꾼다:
  - safetyreport:                tests/fixtures/plate_normalize_vectors.json
  - safetyreport-mobile:         test/fixtures/plate_normalize_vectors.json
  - safetyreport-community-map:  tests/fixtures/plate_normalize_vectors.json (server/plate.ts 가 같은 규칙을 구현)

## 규칙 (경계 규칙 포함, 2026-10-13 이륜 확장 포함)
1. NFKC 정규화 → 공백 전부(스페이스·탭·NBSP·전각공백 등) · 유니코드 서식 문자(Unicode category Cf —
   제로폭 공백/조인터, LRM/RLM, bidi 제어, BOM 등, 실제로 섞여 있어도 보이지 않으므로 통째로 제거) ·
   하이픈(`-`, NFKC 가 전각 하이픈도 반각으로 접어 주므로 이 한 글자만 지우면 된다) 제거.
2. 남은 문자열에서 "지역 접두(17개 시·도 공식명/약칭 — 지도 서버 `server/plate.ts` 의 region 사전과 값이 같다,
   긴 이름부터 시도) + 본문" 패턴과 일치하는 구간을 전부 찾는다.
   본문 = 일반형(숫자 2~3자리 + 한글 1자 + 숫자 4자리, PC `fine_estimate._PLATE_RE`/지도 bodyPattern 과 같음)
        또는 이륜형(숫자 0~1자리 + 한글 1자 + 숫자 0~1자리 + 숫자 4자리, `fine_estimate._MOTORCYCLE_RE` 와 같음).
   지역 필수 이륜형 = 지역 + 시군구(1~3자, 별도 시/군/구 접미 허용) + 글자 + 숫자4.
   숫자 앞 마지막 글자를 용도 글자로 고정하고 시군구 끝 접미 한 글자는 제거한다(1자 지명은 보존).
   접미가 중복되거나 약칭과 지명이 다시 지역 정식명으로 읽혀 정규형이 모호하면 제외한다.
3. 찾은 구간의 바로 앞/뒤 문자가 숫자(0-9) 또는 한글(음절 가-힣, 또는 낱자모 ㄱ-ㅎ/ㅏ-ㅣ)이면 그 구간은 버린다
   ("경계가 애매하면 null") — `81수0136수`(번호판 뒤에 한글 한 글자가 구분자 없이 더 붙음),
   `25어95505`(끝 숫자가 5개 — 4자리를 넘는 초과분이 경계에 붙음), `0006마8861`(지역번호 2~3자리를 넘는
   초과분이 앞 경계에 붙음) 같은 자리수 오류·군더더기를 걸러낸다.
   구두점·기호·영문·다른 언어 문장은 경계로 허용한다 — `177도7967)`, `235호2929/흰색SUV차량입니다.` 는 통과.
   (공백은 2단계에서 모두 지우므로, 설명 문구와 번호판 사이에 공백만 있고 다른 구분자가 없으면 경계가
   사라져 모호로 처리될 수 있다. 실데이터의 설명 붙은 사례는 모두 `/` 로 구분되어 있어 영향이 없었다.)
4. 경계를 통과한 서로 다른 후보 문자열이 정확히 1개면 그 값(지역은 약칭으로 정규화)을 반환한다.
   0개(미인식·날짜·숫자만·자모 섞임·자리수 오류 등) 또는 2개 이상(서로 다른 후보)이면 None — 추측하지 않는다.

입력이 64자를 넘으면(원문 그대로 저장되는 업로드 payload 상한과 같음) None.
"""
from __future__ import annotations

import re
import unicodedata

# 지도 서버 server/plate.ts 의 `regions` 사전과 키/값이 같아야 한다 — 세 레포 공통 정의.
_REGION_MAP = {
    "서울특별시": "서울", "부산광역시": "부산", "대구광역시": "대구", "인천광역시": "인천",
    "광주광역시": "광주", "대전광역시": "대전", "울산광역시": "울산", "세종특별자치시": "세종",
    "경기도": "경기", "강원특별자치도": "강원", "강원도": "강원", "충청북도": "충북",
    "충청남도": "충남", "전북특별자치도": "전북", "전라북도": "전북", "전라남도": "전남",
    "경상북도": "경북", "경상남도": "경남", "제주특별자치도": "제주", "제주도": "제주",
}
for _short in list(_REGION_MAP.values()):
    _REGION_MAP.setdefault(_short, _short)

_REGION_ALT = "|".join(sorted(_REGION_MAP, key=len, reverse=True))
# 일반형(승용/승합/화물/특수/긴급 공통 외형) — fine_estimate._PLATE_RE 의 숫자+한글+숫자 부분과 같다.
_CAR_BODY = r"\d{2,3}[가-힣]\d{4}"
# 이륜형(완화된 형태) — fine_estimate._MOTORCYCLE_RE 와 같다.
_MOTO_BODY = r"\d{0,1}[가-힣]\d{0,1}\d{4}"
_TOKEN_RE = re.compile(rf"({_REGION_ALT})?({_CAR_BODY}|{_MOTO_BODY}|[가-힣]{{2,5}}[0-9]{{4}})")
# 토큰 경계: 숫자 또는 한글(음절/낱자모)이 바로 붙어 있으면 경계가 불분명하다.
_ADJACENT_RE = re.compile(r"[0-9ᄀ-ᇿㄱ-ㆎ가-힣]")
_STRIP_RE = re.compile(r"[-\s]")

_MAX_RAW_LENGTH = 64


def _is_format_char(ch: str) -> bool:
    return unicodedata.category(ch) == "Cf"


def _clean(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text if not _is_format_char(ch))
    return _STRIP_RE.sub("", text)


def normalize_plate(raw) -> str | None:
    """원문에서 유효한 번호판 토큰을 정확히 하나 찾으면 그 값을, 아니면 None 을 돌려준다. 원문은 바꾸지 않는다."""
    if raw is None:
        return None
    text = str(raw)
    if len(text) > _MAX_RAW_LENGTH:
        return None
    text = _clean(text)
    if not text:
        return None

    candidates = set()
    for match in _TOKEN_RE.finditer(text):
        if not match.group(2):
            continue
        start, end = match.span()
        if start > 0 and _ADJACENT_RE.match(text[start - 1]):
            continue
        if end < len(text) and _ADJACENT_RE.match(text[end]):
            continue
        region = _REGION_MAP.get(match.group(1) or "", match.group(1) or "")
        body = match.group(2)
        # 숫자 바로 앞 글자를 고정해 시군구와 용도 글자의 분할을 결정한다.
        if re.fullmatch(r"[가-힣]{2,5}[0-9]{4}", body):
            if not region:
                continue
            city = body[:-5]
            if len(city) > 1 and city[-1] in "시군구":
                city = city[:-1]
            # 접미 제거 뒤에도 복수 접미 해석이 남으면 정규형이 안정되지 않으므로 제외한다.
            if len(city) > 1 and city[-1] in "시군구":
                continue
            if not 1 <= len(city) <= 3:
                continue
            # 약칭+지명 첫 글자가 다시 지역 정식명으로 읽히면 정규형이 모호하다.
            if _REGION_MAP.get(region + city[0]) == region:
                continue
            body = city + body[-5:]
        candidates.add(region + body)

    if len(candidates) == 1:
        return next(iter(candidates))
    return None
