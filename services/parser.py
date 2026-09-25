import re


_C_NOW_STATUS = {0: "진행", 10: "답변완료", 11: "일부수용", 12: "검토중",
                 14: "불수용", 15: "기타", 20: "취하", 30: "이송"}
_CANONICAL_DONE_STATUSES = {"수용", "일부수용", "불수용", "기타", "답변완료", "취하", "이송"}
_FULLWIDTH_TRANSLATION = str.maketrans('０１２３４５６７８９，', '0123456789,')

_NOT_FINAL_ANSWER_STATUSES = ("진행", "처리중", "검토중")  # 이 값이면 다른 칸(C_R_PROC_STAT_NM)·신고 상태로 보완 — 모바일과 같은 규칙

_REJECT_KEYWORDS = ['부득이하게', '종결합니다', '처벌이 어려운 점', '처분이 불가']
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


def parse_json_details(result_data):
    # 1. Body Text Extraction & Regex Parsing
    content_text = result_data.get("C_A_CONTENTS", "")
    if not content_text:
        content_text = result_data.get("C_A_BODY", "")
    content_text_clean = _normalize_raw_payload_text(content_text)
    
    entry_match = re.search(r'본 신고는 안전신문고 (?:앱의|포털의) (.*?) 메뉴로 접수된 신고입니다', content_text_clean)
    entry_value = entry_match.group(1).strip() if entry_match else result_data.get("C_APP_GUBUN_NM", "")
    
    car_number = extract_car_number(content_text_clean)

    occurrence_date_match = re.search(r'발생일자[ \t]*:[ \t]*(\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2})', content_text_clean)
    occurrence_date = re.sub(r'[.\-/]', '-', occurrence_date_match.group(1).strip()) if occurrence_date_match else ""

    occurrence_time_match = re.search(r'발생시각[ \t]*:[ \t]*(\d{2}:\d{2})', content_text_clean)
    occurrence_time = occurrence_time_match.group(1).strip() if occurrence_time_match else ""

    # Extract Violation Location from text or fallback to JSON fields
    violation_location = ""
    if result_data.get("RN_ADRES"):
        violation_location = result_data.get("RN_ADRES")
    elif result_data.get("C_A_ADD2"):
        violation_location = result_data.get("C_A_ADD2")
    else:
        violation_location = str(result_data.get("C_A_ADDR_HEAD") or "") + " " + str(result_data.get("C_A_ADDR_TAIL") or "")
    violation_location = violation_location.strip()

    # 신고자가 보완 제출 완료(SPLMNT_CMPTN_DT 설정)하고 2차 요청 없음(SPLMNT_CMPTN_YN != 'N') 시 갱신
    if result_data.get('SPLMNT_CMPTN_DT') and result_data.get('SPLMNT_CMPTN_YN') != 'N':
        if result_data.get('SPLMNT_VHRNO'):
            car_number = re.sub(r'\s+', '', result_data['SPLMNT_VHRNO'])
        raw_date = str(result_data.get('SPLMNT_DEVEL_DATE') or '')
        if len(raw_date) == 8:
            occurrence_date = f"{raw_date[:4]}-{raw_date[4:6]}-{raw_date[6:]}"
        raw_time = str(result_data.get('SPLMNT_DEVEL_TIME') or '')
        if len(raw_time) >= 4:
            occurrence_time = f"{raw_time[:2]}:{raw_time[2:4]}"
        splmnt_loc = (result_data.get('SPLMNT_RN_ADRES') or result_data.get('SPLMNT_C_A_ADD2') or '').strip()
        if splmnt_loc:
            violation_location = splmnt_loc

    c_now = result_data.get("C_NOW", 0)
    try:
        c_now = int(float(c_now))
    except:
        pass

    answers = result_data.get("answers", []) or []
    splmnt_dmnd_no = result_data.get("SPLMNT_DMND_NO") or 0
    try:
        splmnt_dmnd_no = int(splmnt_dmnd_no)
    except (TypeError, ValueError):
        splmnt_dmnd_no = 0
    splmnt_open = (
        splmnt_dmnd_no > 0
        and result_data.get("SPLMNT_FNSH_YN") == "N"
        and result_data.get("SPLMNT_CMPTN_YN") == "N"
        and not answers
        and c_now == 0
    )

    raw_status = _C_NOW_STATUS.get(c_now, str(c_now) if c_now > 0 else "진행")
    
    report_content = ""
    if content_text_clean:
        parts = re.split(r'\*\s*차량번호', content_text_clean, 1)
        if parts:
            report_content = parts[0].strip()
    
    # 3. Agency Answers & Results Processing
    processing_status = ""
    processing_agency = ""
    person_in_charge = ""
    response_date = ""
    processing_content = ""
    processing_finish = "N"

    if answers:
        latest_ans = answers[-1]
        processing_status = latest_ans.get("C_MANAGER_TYPE_NM") or ""
        if not processing_status or processing_status in _NOT_FINAL_ANSWER_STATUSES:
            processing_status = latest_ans.get("C_R_PROC_STAT_NM") or processing_status

        if not processing_status or processing_status in _NOT_FINAL_ANSWER_STATUSES:
            # If C_NOW indicates completion but agency left status as 진행
            if raw_status in _CANONICAL_DONE_STATUSES:
                processing_status = raw_status
                
        if processing_status in _CANONICAL_DONE_STATUSES:
            processing_finish = "Y"
        # 값이 null 인 키는 "없음"으로 본다(모바일 `??` 와 같게)
        processing_agency = latest_ans.get("C_MANAGE_ORG_NAME") or latest_ans.get("C_MANAGER_TYPE_NM") or ""
        person_in_charge = latest_ans.get("C_MANAGE_MAN") or latest_ans.get("C_R_MOD_ID") or ""
        response_date = latest_ans.get("C_DATE") or latest_ans.get("C_R_MOD_DATE") or ""
        if response_date and len(response_date) >= 10:
             response_date = response_date[:10]
        processing_content = (latest_ans.get("C_MANAGE_CONTENTS") or latest_ans.get("C_R_BODY") or "")
        # Strip HTML tags
        processing_content = re.sub(r'<[^>]+>', '\n', processing_content).strip()
        # 전각 숫자·쉼표·nbsp 정리(과태료 금액·미확인 판정이 흔들리지 않게 — 모바일과 같게)
        processing_content = processing_content.replace("\xa0", " ").translate(_FULLWIDTH_TRANSLATION)
        
    violation_law = ""
    if processing_content:
        violation_law_match = re.search(r'도로교통법\s*제\d+조(?:\s*제?\d{1,2}항)?', processing_content)
        if violation_law_match:
            violation_law = re.sub(r'\s+', '', violation_law_match.group(0)).replace('법제', '법 제')

    fine_entry = ""
    if ("버스전용차로 위반" in entry_value or "쓰레기, 폐기물" in entry_value or "불법주정차신고" in entry_value) and processing_status == "수용":
        fine_entry = "과태료"

    penalty_matches = re.search(r'범칙금\s*([\d,.]+)\s*원[,\s]*벌점\s*(\d{0,4})\s*점', processing_content)
    fine_matches = re.search(r'과태료\s*([\d,.]+)\s*원', processing_content)

    penalty_amount = ""
    penalty_points = ""

    if penalty_matches:
        penalty_amount = "범칙금: " + penalty_matches.group(1) + "원"
        penalty_points = "벌점: " + penalty_matches.group(2) + "점"
    elif fine_matches:
        penalty_amount = "과태료: " + fine_matches.group(1) + "원"
    else:
        penalty_amount = fine_entry

    processing_status, processing_finish, penalty_amount, penalty_points = \
        _apply_penalty_corrections(processing_status, processing_finish,
                                   penalty_amount, penalty_points, entry_value, processing_content)

    # 4. Attachments Mapping
    map_image = ""
    if result_data.get("STTEMNT_IMAGE_URL"):
        map_image = str(result_data.get("STTEMNT_IMAGE_URL"))
        if map_image.startswith('/'):
            map_image = "https://www.safetyreport.go.kr" + map_image
        
    attached_photos = ""
    attachment_files = ""
    files = result_data.get("ARR_C_FILES", result_data.get("files", []))
    img_links = []
    other_links = []
    
    if files:
        for f in files:
            file_url = f.get("FILE_URL")
            if not file_url:
                atch_id = f.get("ATCH_FILE_ID")
                file_url = f"https://www.safetyreport.go.kr/fileDown/singo/{atch_id}" if atch_id else ""
            if not file_url: continue
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

    if map_image:
        # STTEMNT_IMAGE_URL이 ARR_C_FILES에도 포함된 경우 제거 (안전망)
        if map_image in img_links:
            img_links.remove(map_image)

    attached_photos = "\n".join(img_links)
    attachment_files = "\n".join(other_links)

    processing_status = (processing_status or "").strip()
    if raw_status in ("취하", "이송"):
        processing_status = raw_status
    elif processing_status in _CANONICAL_DONE_STATUSES:
        pass
    elif processing_status not in _CANONICAL_DONE_STATUSES and raw_status in _CANONICAL_DONE_STATUSES:
        processing_status = raw_status
    elif splmnt_open:
        processing_status = "보완요청"
    else:
        processing_status = "처리중"

    processing_finish = "Y" if processing_status in _CANONICAL_DONE_STATUSES else "N"
    if processing_status == "취하":
        penalty_amount = ""
        penalty_points = ""

    # JSON 만으로 마지막 round 요약 만들기. 다회차 이력 전체는 보존하지 않음.
    last_round = _build_last_supplement_round_from_json(result_data)
    if last_round and processing_status != "보완요청":
        last_round["is_open"] = "N"
    supplement_summary = _build_supplement_summary(
        [last_round] if last_round else []
    )
    if last_round:
        # 횟수는 SPLMNT_DMND_NO 가 더 정확 (직전 round 까지 누적된 라운드 번호).
        supplement_summary["count"] = max(splmnt_dmnd_no, supplement_summary["count"])

    # title 갱신용 필드 구성
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

    return {
        "entry_value": entry_value,
        "car_number": car_number,
        "occurrence_date": occurrence_date,
        "occurrence_time": occurrence_time,
        "violation_location": violation_location,
        "progress_status": raw_status,
        "processing_status": processing_status,
        "processing_finish": processing_finish,
        "processing_agency": processing_agency,
        "person_in_charge": person_in_charge,
        "response_date": response_date,
        "processing_content": processing_content,
        "violation_law": violation_law,
        "penalty_amount": penalty_amount,
        "penalty_points": penalty_points,
        "report_content": report_content,
        "attachment_files": attachment_files,
        "attached_photos": attached_photos,
        "map_image": map_image,
        "raw_content": content_text_clean,
        "raw_type": "report_body",
        "title_fields": title_fields,
        "supplement_summary": supplement_summary,
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
