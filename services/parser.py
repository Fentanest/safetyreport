import math
import re

from services.report_category import normalize_code
from services.fine_amount_syntax import explicit_fine_number
from services.case_fine_supplement import supplement_case_fine
from services.law_values import supplement_law


# 확인된 코드만 둔다. 13 은 실사용자 응답에서 봤지만 뜻을 확인하지 못해 매핑하지 않는다(2026-10-12 사용자 지시 —
# 근거 없이 답변완료로 붙이지 않는다). 모르는 코드는 숫자 그대로 두고, 답변 내용으로 판정한다(_final_status).
_C_NOW_STATUS = {0: "진행", 10: "답변완료", 11: "일부수용", 12: "검토중",
                 14: "불수용", 15: "기타", 20: "취하", 30: "이송"}
_CANONICAL_DONE_STATUSES = {"수용", "일부수용", "불수용", "기타", "답변완료", "취하", "이송"}
_FULLWIDTH_TRANSLATION = str.maketrans('０１２３４５６７８９，', '0123456789,')

_NOT_FINAL_ANSWER_STATUSES = ("진행", "처리중", "검토중")  # 이 값이면 다른 칸(C_R_PROC_STAT_NM)·신고 상태로 보완 — 모바일과 같은 규칙

_REJECT_KEYWORDS = ['부득이하게', '종결합니다', '처벌이 어려운 점', '처분이 불가']
_PARTIAL_UNKNOWN_MENUS = ("버스전용차로 위반", "쓰레기, 폐기물", "불법주정차신고")
_WARNING_KEYWORDS = ['교통질서 안내장', '훈방권', '증거에 의해서만', '12대 중과실', '82도117', '관리대상으로', '12개 중과실']


def _normalize_raw_payload_text(value) -> str:
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\xa0", " ").translate(_FULLWIDTH_TRANSLATION)
    return text.strip()

def _apply_penalty_corrections(processing_status, processing_finish, penalty_amount, penalty_points, entry_value, text):
    """불수용 강제 교정 및 경고/미확인 설정. text는 담당자 처리내용(processing_content)만 넘길 것."""
    if processing_status not in ("수용", "일부수용") and any(kw in text for kw in _REJECT_KEYWORDS):
        return "불수용", "Y", "", ""
    if not penalty_amount and any(kw in text for kw in _WARNING_KEYWORDS):
        return processing_status, processing_finish, "경고", penalty_points
    if not penalty_amount and "자동차·교통위반" in entry_value and processing_status in ("수용", "일부수용", "기타"):
        return processing_status, processing_finish, "미확인", penalty_points
    # 2026-09-28 사용자 결정: 주정차·버스전용차로·쓰레기 메뉴의 '일부수용'은 보통 과태료 처분이 아니다 — 금액 없는 '과태료' 대신 '미확인'(과태료 미확인).
    if not penalty_amount and processing_status == "일부수용" and any(m in entry_value for m in _PARTIAL_UNKNOWN_MENUS):
        return processing_status, processing_finish, "미확인", penalty_points
    return processing_status, processing_finish, penalty_amount, penalty_points

def _build_supplement_summary(rounds: list[dict] | None) -> dict:
    """round 리스트를 detail row 에 저장할 마지막 보완요청 요약으로 압축한다."""
    if not rounds:
        return {
            "count": 0,
            "is_open": "N",
            "requester": "",
            "requested_at": "",
            "completed_at": "",
            "request_text": "",
            "reporter_opinion": "",
        }
    last = rounds[-1]
    return {
        "count": len(rounds),
        "is_open": last.get("is_open") or "N",
        "requester": (last.get("보완_요청자") or "").strip(),
        "requested_at": (last.get("보완_요청_일시") or "").strip(),
        "completed_at": (last.get("보완_완료_일시") or "").strip(),
        "request_text": (last.get("보완_요청_내용") or "").strip(),
        "reporter_opinion": (last.get("신고자_보완_의견") or "").strip(),
    }


def extract_car_number(content_text: str) -> str:
    """본문의 `차량번호 : …` 한 줄에서 번호만. 같은 줄 안에서만 읽고 `*`·`(위` 에서 끊는다.

    칸이 비어 있으면(`차량번호 : ⏎* 발생일자 …`) 빈 값 — 예전엔 콜론 뒤 줄바꿈을 건너뛰어 다음 줄을 번호로 가져갔다.
    """
    match = re.search(r'차량번호[ \t]*:[ \t]*([^\n]*)', content_text or "")
    if not match:
        return ""
    value = re.split(r'\*|\(위', match.group(1), 1)[0]
    return re.sub(r'\s+', '', value)


def _official_coordinates(data: dict, prefix: str = "") -> tuple[float | None, float | None]:
    """안전신문고 상세 응답의 위도(W)·경도(E)를 한 쌍으로 읽는다."""
    try:
        lat = float(data[f"{prefix}C_A_W"])
        lng = float(data[f"{prefix}C_A_E"])
    except (KeyError, TypeError, ValueError):
        return None, None
    if not (math.isfinite(lat) and math.isfinite(lng) and 32 <= lat <= 39.5 and 124 <= lng <= 132):
        return None, None
    return lat, lng


def _extract_body(result_data) -> dict:
    """1단계: 본문 정리와 본문에서 읽는 값(접수 메뉴·차량번호·발생일시·신고 내용)."""
    content_text = result_data.get("C_A_CONTENTS", "")
    if not content_text:
        content_text = result_data.get("C_A_BODY", "")
    content_text_clean = _normalize_raw_payload_text(content_text)

    entry_match = re.search(r'본 신고는 안전신문고 (?:앱의|포털의) (.*?) 메뉴로 접수된 신고입니다', content_text_clean)
    entry_value = entry_match.group(1).strip() if entry_match else result_data.get("C_APP_GUBUN_NM", "")

    occurrence_date_match = re.search(r'발생일자[ \t]*:[ \t]*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})', content_text_clean)
    occurrence_time_match = re.search(r'발생시각[ \t]*:[ \t]*(\d{2}:\d{2})', content_text_clean)

    report_content = ""
    if content_text_clean:
        parts = re.split(r'\*\s*차량번호', content_text_clean, 1)
        if parts:
            report_content = parts[0].strip()
    return {
        "raw_content": content_text_clean,
        "entry_value": entry_value,
        "car_number": extract_car_number(content_text_clean),
        "occurrence_date": re.sub(r'[.\-/]', '-', occurrence_date_match.group(1).strip()) if occurrence_date_match else "",
        "occurrence_time": occurrence_time_match.group(1).strip() if occurrence_time_match else "",
        "report_content": report_content,
    }


def _apply_completed_supplement(result_data, body: dict) -> dict:
    """1단계(위치): 공식 위치·좌표, 그리고 완료된 보완 제출의 차량번호·발생일시·위치로 갱신한다.
    신고자가 보완 제출 완료(SPLMNT_CMPTN_DT 설정)하고 2차 요청 없음(SPLMNT_CMPTN_YN != 'N') 시 갱신."""
    if result_data.get("RN_ADRES"):
        location = result_data.get("RN_ADRES")
    elif result_data.get("C_A_ADD2"):
        location = result_data.get("C_A_ADD2")
    else:
        location = str(result_data.get("C_A_ADDR_HEAD") or "") + " " + str(result_data.get("C_A_ADDR_TAIL") or "")
    out = dict(body, violation_location=location.strip())
    out["violation_latitude"], out["violation_longitude"] = _official_coordinates(result_data)

    # 차량번호 우선순위(2026-10): 완료된 보완의 SPLMNT_VHRNO(아래) > 상세 응답의 VHRNO(공식 값) > 본문 정규식(기본값).
    # 실샘플: 교통위반 12건은 VHRNO 가 늘 있고 본문과 같았고, 주정차 11건은 VHRNO 가 늘 비어 있어 본문 값을 그대로 썼다.
    vhrno = re.sub(r'\s+', '', str(result_data.get('VHRNO') or ''))
    if vhrno:
        out["car_number"] = vhrno

    if result_data.get('SPLMNT_CMPTN_DT') and result_data.get('SPLMNT_CMPTN_YN') != 'N':
        if result_data.get('SPLMNT_VHRNO'):
            out["car_number"] = re.sub(r'\s+', '', result_data['SPLMNT_VHRNO'])
        raw_date = str(result_data.get('SPLMNT_DEVEL_DATE') or '')
        if len(raw_date) == 8:
            out["occurrence_date"] = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        raw_time = str(result_data.get('SPLMNT_DEVEL_TIME') or '')
        if len(raw_time) >= 4:
            out["occurrence_time"] = f"{raw_time[:2]}:{raw_time[2:4]}"
        splmnt_loc = (result_data.get('SPLMNT_RN_ADRES') or result_data.get('SPLMNT_C_A_ADD2') or '').strip()
        if splmnt_loc:
            out["violation_location"] = splmnt_loc
            # 보완 주소가 채택되면 같은 보완 응답의 좌표만 사용한다.
            # 한쪽이 없거나 잘못된 좌표는 이전 위치와 섞지 않는다.
            out["violation_latitude"], out["violation_longitude"] = _official_coordinates(result_data, 'SPLMNT_')
        else:
            # 주소가 되풀이되지 않아도 보완 좌표만 갱신될 수 있다.
            supplement_lat, supplement_lng = _official_coordinates(result_data, 'SPLMNT_')
            if supplement_lat is not None:
                out["violation_latitude"], out["violation_longitude"] = supplement_lat, supplement_lng
    return out


def _c_now_value(result_data):
    """C_NOW 를 정수로. 바꿀 수 없으면 원래 값을 그대로 둔다(예전 동작 그대로)."""
    c_now = result_data.get("C_NOW", 0)
    try:
        c_now = int(float(c_now))
    except:  # noqa: E722 — 예전 동작(형식이 틀리면 원래 값을 그대로 둔다)
        pass
    return c_now


def _supplement_request_no(result_data) -> int:
    try:
        return int(result_data.get("SPLMNT_DMND_NO") or 0)
    except (TypeError, ValueError):
        return 0


def _select_answer(answers: list, raw_status: str) -> dict:
    """2단계: 마지막 답변 하나를 고른 답변 모델. 답변이 없으면 빈 값."""
    selected = {"processing_status": "", "processing_agency": "", "processing_agency_code": "",
                "person_in_charge": "", "response_date": "", "processing_content": "", "processing_finish": "N"}
    if not answers:
        return selected
    latest_ans = answers[-1]
    processing_status = latest_ans.get("C_MANAGER_TYPE_NM") or ""
    if not processing_status or processing_status in _NOT_FINAL_ANSWER_STATUSES:
        processing_status = latest_ans.get("C_R_PROC_STAT_NM") or processing_status
    if not processing_status or processing_status in _NOT_FINAL_ANSWER_STATUSES:
        # If C_NOW indicates completion but agency left status as 진행
        if raw_status in _CANONICAL_DONE_STATUSES:
            processing_status = raw_status
    selected["processing_status"] = processing_status
    if processing_status in _CANONICAL_DONE_STATUSES:
        selected["processing_finish"] = "Y"
    # 값이 null 인 키는 "없음"으로 본다(모바일 `??` 와 같게)
    selected["processing_agency"] = latest_ans.get("C_MANAGE_ORG_NAME") or latest_ans.get("C_MANAGER_TYPE_NM") or ""
    # 기관코드 원문(TEXT): 같은 선택 답변의 C_MANAGE_ORG. 7자리 영숫자·선행 0 보존, 정수 변환 금지.
    # 없으면 ""(저장 단계에서 NULL). 이름으로 찾은 후보를 여기에 쓰지 않는다.
    selected["processing_agency_code"] = str(latest_ans.get("C_MANAGE_ORG") or "").strip()
    selected["person_in_charge"] = latest_ans.get("C_MANAGE_MAN") or latest_ans.get("C_R_MOD_ID") or ""
    response_date = latest_ans.get("C_DATE") or latest_ans.get("C_R_MOD_DATE") or ""
    if response_date and len(response_date) >= 10:
        response_date = response_date[:10]
    selected["response_date"] = response_date
    processing_content = (latest_ans.get("C_MANAGE_CONTENTS") or latest_ans.get("C_R_BODY") or "")
    # Strip HTML tags
    processing_content = re.sub(r'<[^>]+>', '\n', processing_content).strip()
    # 전각 숫자·쉼표·nbsp 정리(과태료 금액·미확인 판정이 흔들리지 않게 — 모바일과 같게)
    selected["processing_content"] = processing_content.replace("\xa0", " ").translate(_FULLWIDTH_TRANSLATION)
    return selected


# The legacy regex runs on both platforms (Dart: law_values.legacyViolationLaw,
# non-unicode JS-style RegExp). Python \s and \d differ from Dart, so the
# Dart whitespace set and ASCII digits are spelled out here.
_JS_SPACE = '[\\t\\n\\x0b\\x0c\\r \\xa0\\u1680\\u2000-\\u200a\\u2028\\u2029\\u202f\\u205f\\u3000\\ufeff]'
_LEGACY_LAW = re.compile(
    '(?:「' + _JS_SPACE + '*([가-힣·' + _JS_SPACE[1:-1] + ']{1,40}?법)' + _JS_SPACE + '*」|(도로교통법))' + _JS_SPACE
    + '*제' + _JS_SPACE + '*([0-9]+' + _JS_SPACE + '*조(?:' + _JS_SPACE + '*의' + _JS_SPACE + '*[0-9]+)?)(?:'
    + _JS_SPACE + '*(제?' + _JS_SPACE + '*[0-9]{1,2}' + _JS_SPACE + '*항))?')
_LEGACY_SPACES = re.compile(_JS_SPACE + '+')


def _legacy_violation_law(processing_content: str) -> str:
    """3단계: 답변 본문의 위반 법규(법 이름 + 조·항)."""
    if not processing_content:
        return ""
    violation_law_match = _LEGACY_LAW.search(processing_content)
    if not violation_law_match:
        return ""
    law_name = _LEGACY_SPACES.sub('', violation_law_match.group(1) or violation_law_match.group(2))
    article = _LEGACY_SPACES.sub('', violation_law_match.group(3))
    paragraph = _LEGACY_SPACES.sub('', violation_law_match.group(4) or '')
    # '제32조의2 1항': removing the whitespace would merge the branch number and
    # the paragraph number ('의21항'). Keep the boundary with '제' only when two
    # digits would touch; '제13조3항' and a source-joined '제32조의21항' keep
    # their existing notation. Same rule in Dart law_values.legacyViolationLaw.
    if paragraph[:1].isdigit() and article[-1:].isdigit():
        paragraph = '제' + paragraph
    return f"{law_name} 제{article}{paragraph}"


def _violation_law(processing_content: str) -> str:
    """Recognize with the existing parser, then supplement/validate its result."""
    return supplement_law(processing_content, _legacy_violation_law(processing_content))


def _penalty(entry_value: str, processing_status: str, processing_content: str) -> tuple[str, str]:
    """3단계: 답변 본문의 범칙금·벌점·과태료. 없으면 과태료 메뉴의 수용은 '과태료'."""
    fine_entry = ""
    if ("버스전용차로 위반" in entry_value or "쓰레기, 폐기물" in entry_value or "불법주정차신고" in entry_value) \
            and processing_status == "수용":
        fine_entry = "과태료"
    penalty_matches = re.search(r'범칙금\s*([\d,.]+)\s*원[,\s]*벌점\s*(\d{0,4})\s*점', processing_content)
    fine_number = explicit_fine_number(processing_content)
    if penalty_matches:
        return "범칙금: " + penalty_matches.group(1) + "원", "벌점: " + penalty_matches.group(2) + "점"
    if fine_number is not None:
        return "과태료: " + fine_number + "원", ""
    return fine_entry, ""


def _attachments(result_data) -> dict:
    """1단계(첨부): 지도 이미지·사진·그 밖의 첨부 링크."""
    map_image = ""
    if result_data.get("STTEMNT_IMAGE_URL"):
        map_image = str(result_data.get("STTEMNT_IMAGE_URL"))
        if map_image.startswith('/'):
            map_image = "https://www.safetyreport.go.kr" + map_image
    files = result_data.get("ARR_C_FILES", result_data.get("files", []))
    img_links = []
    other_links = []
    if files:
        for f in files:
            file_url = f.get("FILE_URL")
            if not file_url:
                atch_id = f.get("ATCH_FILE_ID")
                file_url = f"https://www.safetyreport.go.kr/fileDown/singo/{atch_id}" if atch_id else ""
            if not file_url:
                continue
            if file_url.startswith('/'):
                file_url = "https://www.safetyreport.go.kr" + file_url
            # FILE_TY: 1 (img) / 3 (img) / 8 (img) / 2 (video) / 99 (other)
            file_ty = str(f.get("FILE_TY", ""))
            original_nm = (f.get("ORGINL_FILE_NM") or "").lower()
            if original_nm:
                ext = original_nm.split('.')[-1]
            else:
                ext = (f.get("FILE_EXTSN") or f.get("EXT") or "").lower()
            if "MAPIMG" in file_url:
                # 지도 이미지 — STTEMNT_IMAGE_URL이 없을 때만 fallback으로 사용
                if not map_image:
                    map_image = file_url
                # img_links/other_links에 포함하지 않음 (레거시 파서와 동일)
            elif file_ty in ["1", "3", "8"] or ext in ['jpg', 'jpeg', 'png', 'gif', 'bmp']:
                img_links.append(file_url)
            else:
                other_links.append(file_url)
    if map_image and map_image in img_links:  # STTEMNT_IMAGE_URL이 ARR_C_FILES에도 포함된 경우 제거 (안전망)
        img_links.remove(map_image)
    return {"map_image": map_image, "attached_photos": "\n".join(img_links), "attachment_files": "\n".join(other_links)}


def _final_status(processing_status: str, raw_status: str, supplement_open: bool, answered_band: bool = False) -> str:
    """3단계: 표시 상태(canonical). 취하·이송은 진행 상태가 정하고, 답변이 끝난 상태가 아니면 진행 상태 → 보완요청 → 처리중.
    answered_band: 답변이 있고 C_NOW 가 10~19(답변완료 계열)인데 선택한 답변 상태도, raw_status 도 모르는 값일 때
    '처리중'으로 떨어뜨리지 않는 안전망(2026-10, 답변완료인데 진행중으로 보이던 버그 — 새 canonical 값은 만들지 않고 '답변완료'로만 닫는다)."""
    processing_status = (processing_status or "").strip()
    if raw_status in ("취하", "이송"):
        return raw_status
    if processing_status in _CANONICAL_DONE_STATUSES:
        return processing_status
    if raw_status in _CANONICAL_DONE_STATUSES:
        return raw_status
    if supplement_open:
        return "보완요청"
    if answered_band and processing_status not in _NOT_FINAL_ANSWER_STATUSES:
        # 답변이 스스로 진행·처리중·검토중이라고 밝히면 모르는 C_NOW(16~19)라도 종결하지 않는다(Astra 재검수 검토 항목 3).
        return "답변완료"
    return "처리중"


def _supplement(result_data, processing_status: str, supplement_request_no: int) -> dict:
    """4단계: JSON 만으로 마지막 round 요약. 다회차 이력 전체는 보존하지 않음."""
    last_round = _build_last_supplement_round_from_json(result_data)
    if last_round and processing_status != "보완요청":
        last_round["is_open"] = "N"
    summary = _build_supplement_summary([last_round] if last_round else [])
    if last_round:
        # 횟수는 SPLMNT_DMND_NO 가 더 정확 (직전 round 까지 누적된 라운드 번호).
        summary["count"] = max(supplement_request_no, summary["count"])
    return summary


def _title_fields(result_data, raw_status: str) -> dict:
    """4단계: 목록(title) 갱신용 필드."""
    c_now_int = result_data.get('C_NOW', 0)
    try:
        c_now_int = int(float(c_now_int))
    except Exception:
        c_now_int = 0
    try:
        stsfdg = int(float(result_data.get('STSFDG_SCORE') or 0))
    except (TypeError, ValueError):
        stsfdg = 0
    if stsfdg > 0:
        poll_status = '참여 완료'
    elif c_now_int in (10, 11, 14, 15):
        poll_status = '참여 가능'
    elif c_now_int in (20, 30):
        poll_status = '참여 불가'
    else:
        poll_status = '답변 대기'
    title_raw = result_data.get('C_A_TITLE') or ''
    title_text = title_raw.split(')', 1)[-1].strip() if ')' in title_raw else title_raw.strip()
    report_date = (result_data.get('C_DATE', '') or '').split()
    title_fields = {
        '상태': raw_status,
        '신고번호': result_data.get('STTEMNT_NO') or '',
        '신고명': title_text,
        '신고일': report_date[0] if report_date else '',
        '만족도조사여부': poll_status,
    }
    if stsfdg > 0:
        # 상세 응답의 만족도 점수도 사이트 값이다(사용자 결정 2026-09-25). 사유는 만족도 조회로만 채운다.
        title_fields['별점'] = stsfdg
    return title_fields


def parse_json_details(result_data):
    """안전신문고 상세 JSON → 저장용 dict(EO R-11: 순수 단계로 나눔, 반환 모양은 그대로).
    1 본문·위치·첨부 추출 → 2 선택 답변 → 3 법규·처분·상태 판정 → 4 보완 요약·목록 갱신 조립.
    모바일 standalone_parser.dart 와 같은 규칙(contracts/parser-vectors.json)."""
    located = _apply_completed_supplement(result_data, _extract_body(result_data))

    c_now = _c_now_value(result_data)
    answers = result_data.get("answers", []) or []
    supplement_request_no = _supplement_request_no(result_data)
    supplement_open = (
        supplement_request_no > 0
        and result_data.get("SPLMNT_FNSH_YN") == "N"
        and result_data.get("SPLMNT_CMPTN_YN") == "N"
        and not answers
        and c_now == 0
    )
    raw_status = _C_NOW_STATUS.get(c_now, str(c_now) if c_now > 0 else "진행")
    # 답변완료 대역(C_NOW 10~19)에 답변이 있는데 상태를 못 알아보면 '처리중'으로 떨어지지 않게 한다.
    # 단, C_NOW 가 명시적으로 비종결 상태(진행·처리중·검토중, 예: 12=검토중)로 매핑되면 이 안전망이
    # 그 상태를 덮어 '답변완료'로 조기 종결시키지 않는다(Astra 검토 항목 3). 16~19처럼 매핑이 없는 코드는
    # 선택한 답변의 상태도 비종결이 아닐 때(빈 값·모르는 라벨)만 닫는다 — `_final_status` 참고.
    answered_band = bool(answers) and 10 <= c_now <= 19 and raw_status not in _NOT_FINAL_ANSWER_STATUSES

    answer = _select_answer(answers, raw_status)
    processing_content = answer["processing_content"]
    penalty_amount, penalty_points = _penalty(located["entry_value"], answer["processing_status"], processing_content)
    processing_status, processing_finish, penalty_amount, penalty_points = \
        _apply_penalty_corrections(answer["processing_status"], answer["processing_finish"],
                                   penalty_amount, penalty_points, located["entry_value"], processing_content)
    attachments = _attachments(result_data)

    processing_status = _final_status(processing_status, raw_status, supplement_open, answered_band)
    processing_finish = "Y" if processing_status in _CANONICAL_DONE_STATUSES else "N"
    if processing_status == "취하":
        penalty_amount = ""
        penalty_points = ""

    processing_status, penalty_amount = supplement_case_fine(
        processing_status, penalty_amount, processing_content)

    # 분류용 원시 코드. report_category.normalize_code 로 정규화(대문자·두 자리·빈 값은 None)만 하고 분류 자체는 하지 않는다.
    ssnpc_cd, ssnpc_type = normalize_code(result_data.get("SSNPC_CD"), result_data.get("SSNPC_TYPE"))

    return {
        "entry_value": located["entry_value"],
        "ssnpc_cd": ssnpc_cd,
        "ssnpc_type": ssnpc_type,
        "car_number": located["car_number"],
        "occurrence_date": located["occurrence_date"],
        "occurrence_time": located["occurrence_time"],
        "violation_location": located["violation_location"],
        "violation_latitude": located["violation_latitude"],
        "violation_longitude": located["violation_longitude"],
        "progress_status": raw_status,
        "processing_status": processing_status,
        "processing_finish": processing_finish,
        "processing_agency": answer["processing_agency"],
        "processing_agency_code": answer["processing_agency_code"],
        "person_in_charge": answer["person_in_charge"],
        "response_date": answer["response_date"],
        "processing_content": processing_content,
        "violation_law": _violation_law(processing_content),
        "penalty_amount": penalty_amount,
        "penalty_points": penalty_points,
        "report_content": located["report_content"],
        "attachment_files": attachments["attachment_files"],
        "attached_photos": attachments["attached_photos"],
        "map_image": attachments["map_image"],
        "raw_content": located["raw_content"],
        "raw_type": "report_body",
        "title_fields": _title_fields(result_data, raw_status),
        "supplement_summary": _supplement(result_data, processing_status, supplement_request_no),
    }


def _format_epoch_millis_to_datetime(epoch_ms) -> str:
    if not epoch_ms:
        return ""
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(int(epoch_ms) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return ""


def _build_last_supplement_round_from_json(result_data: dict) -> dict | None:
    """API JSON 응답에서 가장 최근(=현재) round 한 건만 dict 로 만든다.

    SPA 의 다회차 보완 history 전체는 별도 AJAX 가 채우므로 JSON 만으로는 알 수 없다.
    프로젝트 정책상 마지막 round 1개와 누적 횟수만 보존한다.
    """
    dmnd_no = result_data.get("SPLMNT_DMND_NO") or 0
    try:
        dmnd_no = int(dmnd_no)
    except (TypeError, ValueError):
        dmnd_no = 0
    if dmnd_no <= 0 and not result_data.get("SPLMNT_DMND_CONTENTS") and not result_data.get("SPLMNT_CMPTN_DT"):
        return None

    is_open = (
        "Y"
        if result_data.get("SPLMNT_FNSH_YN") == "N"
        and result_data.get("SPLMNT_CMPTN_YN") == "N"
        else "N"
    )
    return {
        "round_no": max(dmnd_no, 1),
        "보완_요청자": result_data.get("SPLMNT_RQSTR") or "",
        "보완_요청자_연락처": _format_phone(result_data.get("SPLMNT_RQSTR_TELNO")),
        "보완_요청_일시": _format_epoch_millis_to_datetime(result_data.get("SPLMNT_DMND_DT")),
        "보완_완료_일시": _format_epoch_millis_to_datetime(result_data.get("SPLMNT_FNSH_DT")),
        "보완_요청_내용": result_data.get("SPLMNT_DMND_CONTENTS") or "",
        "신고자_보완_의견": ""
        if is_open == "Y"
        else (result_data.get("SPLMNT_ANS_CONTENTS") or ""),
        "is_open": is_open,
    }


def _format_phone(value) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    digits = re.sub(r"\D", "", text)
    if len(digits) == 10 and digits.startswith(("02",)):
        return f"{digits[:2]}-{digits[2:6]}-{digits[6:]}"
    if len(digits) == 11:
        return f"{digits[:3]}-{digits[3:7]}-{digits[7:]}"
    if len(digits) == 10:
        return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"
    return text
