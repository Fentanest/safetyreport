import re

def _parse_report_content_table(driver, report_soup):
    content_th = report_soup.find('th', string='내용')
    content_text = ""
    if content_th:
        content_td = content_th.find_next_sibling('td')
        if content_td:
            content_text = content_td.get_text(separator='\n').translate(str.maketrans('０１２３４５６７８９，', '0123456789,'))

    entry_match = re.search(r'본 신고는 안전신문고 (?:앱의|포털의) (.*?) 메뉴로 접수된 신고입니다', content_text)
    entry_value = entry_match.group(1).strip() if entry_match else ""

    car_number_match = re.search(r'차량번호\s*:\s*(.*?)(?=\n|\(위)', content_text)
    car_number_value = re.sub(r'\s+', '', car_number_match.group(1)) if car_number_match else ""

    occurrence_date_match = re.search(r'발생일자\s*:\s*(\d{4}.\d{1,2}.\d{1,2})', content_text)
    occurrence_date_value = occurrence_date_match.group(1).strip().replace('.', '-') if occurrence_date_match else ""

    occurrence_time_match = re.search(r'발생시각\s*:\s*(\d{2}:\d{2})', content_text)
    occurrence_time_value = occurrence_time_match.group(1).strip() if occurrence_time_match else ""

    violation_location_th = report_soup.find('th', string='신고발생지역')
    violation_location_value = ""
    if violation_location_th:
        violation_location_td = violation_location_th.find_next_sibling('td')
        if violation_location_td and violation_location_td.find('p'):
            violation_location_value = violation_location_td.find('p').get_text(strip=True)

    progress_status_th = report_soup.find('th', string='진행상황')
    progress_status = ""
    if progress_status_th:
        progress_status_td = progress_status_th.find_next_sibling('td')
        if progress_status_td:
            progress_status = progress_status_td.get_text(strip=True)

    report_content = ""
    if content_text:
        parts = re.split(r'\*\s*차량번호', content_text, 1)
        if parts:
            report_content = parts[0].strip()

    attachment_th = report_soup.find('th', string='첨부파일')
    attachment_files = ""
    attached_photos = ""
    map_image = ""
    if attachment_th:
        attachment_td = attachment_th.find_next_sibling('td')
        if attachment_td:
            if "6개월 지난 신고건의 경우 첨부파일을 삭제하고 있습니다." in attachment_td.get_text():
                attachment_files = ""
                attached_photos = ""
            else:
                image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp']
                image_urls = []
                other_urls = []
                map_urls = []
                links = attachment_td.find_all('a')
                for link in links:
                    href = link.get('href')
                    data_title = link.get('data-title')
                    onclick = link.get('onclick')
                    url_path = None
                    
                    if href and href.startswith('/fileDown/singo'):
                        url_path = href
                    elif data_title and data_title.startswith('/fileDown/singo'):
                        url_path = data_title
                    elif onclick and "goViewer('" in onclick:
                        match = re.search(r"goViewer\('([^']+)'\)", onclick)
                        if match:
                            url_path = match.group(1)
                    
                    if url_path:
                        url = f"https://www.safetyreport.go.kr{url_path}"
                        if "MAPIMG" in url:
                            map_urls.append(url)
                        elif any(url.lower().endswith(ext) for ext in image_extensions):
                            image_urls.append(url)
                        else:
                            other_urls.append(url)

                attachment_files = "\n".join(other_urls)
                # 지도(MAPIMG)는 별도 '지도' 컬럼에만 저장 — 첨부사진에 중복 포함하지 않음
                attached_photos = "\n".join(image_urls)
                map_image = "\n".join(map_urls)

    return {
        "entry_value": entry_value,
        "car_number": car_number_value,
        "occurrence_date": occurrence_date_value,
        "occurrence_time": occurrence_time_value,
        "violation_location": violation_location_value,
        "progress_status": progress_status,
        "report_content": report_content,
        "attachment_files": attachment_files,
        "attached_photos": attached_photos,
        "map_image": map_image,
        # title 갱신용 원시 데이터 (신고번호, 신고명, 신고일은 page_soup에서)
        "_report_number_raw": report_soup.find('th', string='신고번호').find_next_sibling('td').get_text(strip=True)
            if report_soup.find('th', string='신고번호') else "",
        "_title_raw": report_soup.find('th', string='제목').find_next_sibling('td').get_text(strip=True)
            if report_soup.find('th', string='제목') else "",
        "_date_raw": report_soup.find('th', string='신고일시').find_next_sibling('td').get_text(strip=True)
            if report_soup.find('th', string='신고일시') else "",
    }

def _parse_processing_result_table(result_soup, entry_value):
    result_text = result_soup.get_text().translate(str.maketrans('０１２３４５６７８９，', '0123456789,'))

    processing_content_th = result_soup.find('th', string='처리내용')
    processing_content = ""
    if processing_content_th:
        processing_content_td = processing_content_th.find_next_sibling('td')
        if processing_content_td:
            processing_content = processing_content_td.get_text(separator='\n').strip()

    violation_law_match = re.search(r'도로교통법\s*제\d+조(?:\s*제?\d{1,2}항)?', result_text)
    if violation_law_match:
        violation_law_value = re.sub(r'\s+', '', violation_law_match.group(0)).replace('법제', '법 제')
    else:
        violation_law_value = ""

    processing_status_th = result_soup.find('th', string='처리상태')
    processing_status_text = ""
    if processing_status_th:
        processing_status_td = processing_status_th.find_next_sibling('td')
        if processing_status_td:
            processing_status_text = processing_status_td.get_text(strip=True)
    
    processing_finish_text = "N"
    if processing_status_text in ["수용", "불수용", "일부수용", "기타", "검토중"]:
        processing_finish_text = "Y"

    processing_agency_th = result_soup.find('th', string='처리기관')
    processing_agency_text = ""
    if processing_agency_th:
        processing_agency_td = processing_agency_th.find_next_sibling('td')
        if processing_agency_td:
            processing_agency_text = processing_agency_td.get_text(strip=True)
    
    person_in_charge_th = result_soup.find('th', string='담당자')
    person_in_charge_text = ""
    if person_in_charge_th:
        person_in_charge_td = person_in_charge_th.find_next_sibling('td')
        if person_in_charge_td:
            person_in_charge_text = person_in_charge_td.get_text(strip=True)

    response_date_th = result_soup.find('th', string='답변일')
    response_date_text = ""
    if response_date_th:
        response_date_td = response_date_th.find_next_sibling('td')
        if response_date_td:
            response_date_text = response_date_td.get_text(strip=True)

    fine_entry = ""
    if ("버스전용차로 위반" in entry_value or "쓰레기, 폐기물" in entry_value or "불법주정차신고" in entry_value) and processing_status_text == "수용":
        fine_entry = "과태료"

    penalty_matches = re.search(r'범칙금\s*([\d,.]+)\s*원, 벌점\s*(\d{0,4})\s*점', result_text)
    fine_matches = re.search(r'과태료\s*([\d,.]+)\s*원', result_text)

    penalty_amount = ""
    penalty_points = ""
    fine_amount = ""

    if penalty_matches:
        penalty_amount = "범칙금: " + penalty_matches.group(1) + "원"
        penalty_points = "벌점: " + penalty_matches.group(2) + "점"
    elif fine_matches:
        fine_amount = "과태료: " + fine_matches.group(1) + "원"
    
    final_penalty = penalty_amount or fine_amount or fine_entry

    reject_keywords = ['부득이하게', '종결합니다', '처벌이 어려운 점', '처분이 불가']
    is_rejected = any(kw in processing_content or kw in result_text for kw in reject_keywords)

    warning_keywords = ['교통질서 안내장', '훈방권', '증거에 의해서만', '12대 중과실', '82도117', '관리대상으로', '12개 중과실']

    if is_rejected and processing_status_text not in ("수용", "일부수용"):
        processing_status_text = "불수용"
        processing_finish_text = "Y"
        final_penalty = ""
    elif not final_penalty and any(kw in processing_content or kw in result_text for kw in warning_keywords):
        final_penalty = '경고'
    elif not final_penalty and "자동차·교통위반" in entry_value and processing_status_text in ("수용", "일부수용", "기타"):
        # 교통위반 답변 완료 후 과태료/범칙금을 파싱할 수 없는 경우 → 미확인
        final_penalty = "미확인"

    return {
        "processing_status": processing_status_text,
        "violation_law": violation_law_value,
        "penalty_amount": final_penalty,
        "penalty_points": penalty_points,
        "processing_agency": processing_agency_text,
        "person_in_charge": person_in_charge_text,
        "response_date": response_date_text,
        "processing_content": processing_content,
        "processing_finish": processing_finish_text,
    }

def _extract_supplement_overrides_from_html(page_soup):
    """splmntDivBody의 마지막 보완 테이블이 완료 확정이면 수정 필드 반환, 아니면 None."""
    if not page_soup:
        return None
    splmnt_div = page_soup.find('div', id='splmntDivBody')
    if not splmnt_div:
        return None
    tables = splmnt_div.find_all('table')
    if not tables:
        return None
    last_text = tables[-1].get_text(' ', strip=True)
    import re as _re
    if not _re.search(r'보완 완료 일시\s+\d{4}-\d{2}-\d{2}', last_text):
        return None
    opinion_match = _re.search(r'신고자 보완 의견\s+(.+?)(?:신고자 보완 첨부파일|$)', last_text, _re.DOTALL)
    if not opinion_match:
        return None
    opinion = opinion_match.group(1).strip()
    result = {}
    m = _re.search(r'차량번호\s*:\s*(.*?)(?=\*|\Z|\n)', opinion)
    if m:
        result['car_number'] = _re.sub(r'\s+', '', m.group(1))
    m = _re.search(r'발생일자\s*:\s*(\d{4})\.(\d{1,2})\.(\d{1,2})\.?', opinion)
    if m:
        result['occurrence_date'] = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
    m = _re.search(r'발생시각\s*:\s*(\d{2}:\d{2})', opinion)
    if m:
        result['occurrence_time'] = m.group(1)
    m = _re.search(r'위반장소\s*:\s*(.+?)(?=\*|\Z|\n)', opinion)
    if m:
        result['violation_location'] = m.group(1).strip()
    return result if result else None


def _extract_poll_status_from_html(page_soup, progress_status):
    """btnArea 버튼 id와 진행상황으로 만족도조사여부 결정."""
    if page_soup:
        btn_area = page_soup.find('div', id='btnArea')
        if btn_area:
            if btn_area.find('button', id='comptStfnLink'):
                return '참여 완료'
            if btn_area.find('button', id='stfnLink'):
                return '참여 가능'
    if progress_status in ('취하', '이송'):
        return '참여 불가'
    return '답변 대기'


def parse_details(driver, report_soup, result_soup=None, page_soup=None):
    report_details = _parse_report_content_table(driver, report_soup)
    progress_status = report_details.get("progress_status", "")

    processing_details = {}
    if result_soup:
        processing_details = _parse_processing_result_table(result_soup, report_details["entry_value"])
    else:
        processing_details = {
            "processing_status": "처리중",
            "violation_law": "",
            "penalty_amount": "",
            "penalty_points": "",
            "processing_agency": "",
            "person_in_charge": "",
            "response_date": "",
            "processing_content": "",
            "processing_finish": "N",
        }

    if progress_status == "취하":
        processing_details["processing_finish"] = "Y"
        processing_details["processing_status"] = "취하"
        processing_details["penalty_amount"] = ""
        processing_details["penalty_points"] = ""

    all_details = {**report_details, **processing_details}

    # 보완 완료 확정 시 신고 정보 갱신 (레거시 HTML 방식)
    splmnt = _extract_supplement_overrides_from_html(page_soup)
    if splmnt:
        for field in ('car_number', 'occurrence_date', 'occurrence_time', 'violation_location'):
            if field in splmnt:
                all_details[field] = splmnt[field]

    # title 갱신용 필드 구성
    report_number = all_details.pop("_report_number_raw", "")
    title_raw = all_details.pop("_title_raw", "")
    date_raw = all_details.pop("_date_raw", "")
    title_text = title_raw.split(')', 1)[-1].strip() if ')' in title_raw else title_raw

    all_details["title_fields"] = {
        "상태": progress_status,
        "신고번호": report_number,
        "신고명": title_text,
        "신고일": date_raw,
        "만족도조사여부": _extract_poll_status_from_html(page_soup, progress_status),
    }

    return all_details

def parse_json_details(result_data):
    # 1. Body Text Extraction & Regex Parsing
    content_text = result_data.get("C_A_CONTENTS", "")
    if not content_text:
        content_text = result_data.get("C_A_BODY", "")
    content_text_clean = content_text.translate(str.maketrans('０１２３４５６７８９，', '0123456789,'))
    
    import re
    entry_match = re.search(r'본 신고는 안전신문고 (?:앱의|포털의) (.*?) 메뉴로 접수된 신고입니다', content_text_clean)
    entry_value = entry_match.group(1).strip() if entry_match else result_data.get("C_APP_GUBUN_NM", "")
    
    car_number_match = re.search(r'차량번호\s*:\s*(.*?)(?=\n|\(위)', content_text_clean)
    car_number = re.sub(r'\s+', '', car_number_match.group(1)) if car_number_match else ""

    occurrence_date_match = re.search(r'발생일자\s*:\s*(\d{4}.\d{1,2}.\d{1,2})', content_text_clean)
    occurrence_date = occurrence_date_match.group(1).strip().replace('.', '-') if occurrence_date_match else ""

    occurrence_time_match = re.search(r'발생시각\s*:\s*(\d{2}:\d{2})', content_text_clean)
    occurrence_time = occurrence_time_match.group(1).strip() if occurrence_time_match else ""

    # Extract Violation Location from text or fallback to JSON fields
    violation_location = ""
    if result_data.get("RN_ADRES"):
        violation_location = result_data.get("RN_ADRES")
    elif result_data.get("C_A_ADD2"):
        violation_location = result_data.get("C_A_ADD2")
    else:
        violation_location = str(result_data.get("C_A_ADDR_HEAD", "")) + " " + str(result_data.get("C_A_ADDR_TAIL", ""))
    violation_location = violation_location.strip()

    # 보완 완료(SPLMNT_CMPTN_YN == 'Y') 시 기관 확인된 최종 신고 정보로 갱신
    if result_data.get('SPLMNT_CMPTN_YN') == 'Y':
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
        
    process_status = "진행"
    if c_now == 10: process_status = "답변완료"
    elif c_now == 11: process_status = "일부수용"
    elif c_now == 12: process_status = "검토중"
    elif c_now == 14: process_status = "불수용"
    elif c_now == 15: process_status = "기타"
    elif c_now == 20: process_status = "취하"
    elif c_now == 30: process_status = "이송"
    elif c_now > 0: process_status = str(c_now)
    
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
    
    answers = result_data.get("answers", [])
    if answers:
        latest_ans = answers[-1]
        processing_status = latest_ans.get("C_MANAGER_TYPE_NM")
        if not processing_status or processing_status in ["진행", "처리중"]:
            processing_status = latest_ans.get("C_R_PROC_STAT_NM", processing_status)
            
        if not processing_status or processing_status in ["진행", "처리중"]:
            # If C_NOW indicates completion but agency left status as 진행
            if process_status in ["답변완료", "수용", "불수용", "일부수용", "기타"]:
                processing_status = process_status
                
        if processing_status in ["수용", "불수용", "일부수용", "기타", "검토중", "답변완료"]:
            processing_finish = "Y"
        processing_agency = latest_ans.get("C_MANAGE_ORG_NAME", latest_ans.get("C_MANAGER_TYPE_NM", ""))
        person_in_charge = latest_ans.get("C_MANAGE_MAN", latest_ans.get("C_R_MOD_ID", ""))
        response_date = latest_ans.get("C_DATE", latest_ans.get("C_R_MOD_DATE", ""))
        if response_date and len(response_date) >= 10:
             response_date = response_date[:10]
        processing_content = (latest_ans.get("C_MANAGE_CONTENTS") or latest_ans.get("C_R_BODY") or "")
        # Strip HTML tags
        processing_content = re.sub(r'<[^>]+>', '\n', processing_content).strip()
        
    violation_law = ""
    if processing_content:
        violation_law_match = re.search(r'도로교통법\s*제\d+조(?:\s*제?\d{1,2}항)?', processing_content)
        if violation_law_match:
            violation_law = re.sub(r'\s+', '', violation_law_match.group(0)).replace('법제', '법 제')

    # 범칙금/과태료: 레거시와 동일하게 실제 금액 정규식 추출
    full_text = processing_content + "\n" + content_text_clean

    fine_entry = ""
    if ("버스전용차로 위반" in entry_value or "쓰레기, 폐기물" in entry_value or "불법주정차신고" in entry_value) and processing_status == "수용":
        fine_entry = "과태료"

    penalty_matches = re.search(r'범칙금\s*([\d,.]+)\s*원[,\s]*벌점\s*(\d{0,4})\s*점', full_text)
    fine_matches = re.search(r'과태료\s*([\d,.]+)\s*원', full_text)

    penalty_amount = ""
    penalty_points = ""

    if penalty_matches:
        penalty_amount = "범칙금: " + penalty_matches.group(1) + "원"
        penalty_points = "벌점: " + penalty_matches.group(2) + "점"
    elif fine_matches:
        penalty_amount = "과태료: " + fine_matches.group(1) + "원"
    else:
        penalty_amount = fine_entry

    # 불수용 키워드 감지 → 상태 강제 교정 + 범칙금 초기화
    reject_keywords = ['부득이하게', '종결합니다', '처벌이 어려운 점', '처분이 불가']
    warning_keywords = ['교통질서 안내장', '훈방권', '증거에 의해서만', '12대 중과실', '82도117', '관리대상으로', '12개 중과실']

    if processing_status not in ("수용", "일부수용") and any(kw in full_text for kw in reject_keywords):
        processing_status = "불수용"
        processing_finish = "Y"
        penalty_amount = ""
        penalty_points = ""
    elif not penalty_amount and any(kw in full_text for kw in warning_keywords):
        penalty_amount = "경고"
    elif not penalty_amount and "자동차·교통위반" in entry_value and processing_status in ("수용", "일부수용", "기타"):
        # 교통위반 답변 완료 후 과태료/범칙금을 파싱할 수 없는 경우 → 미확인
        penalty_amount = "미확인"

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
            original_nm = f.get("ORGINL_FILE_NM", "").lower()
            if original_nm:
                ext = original_nm.split('.')[-1]
            else:
                ext = f.get("FILE_EXTSN", f.get("EXT", "")).lower()
                
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

    if not processing_status:
        processing_status = "처리중"

    if process_status == "취하":
        processing_finish = "Y"
        processing_status = "취하"
        penalty_amount = ""
        penalty_points = ""

    # title 갱신용 필드 구성
    c_now_int = result_data.get('C_NOW', 0)
    try:
        c_now_int = int(float(c_now_int))
    except Exception:
        c_now_int = 0
    stsfdg = int(result_data.get('STSFDG_SCORE', 0) or 0)
    if stsfdg > 0:
        poll_status = '참여 완료'
    elif c_now_int in (10, 11, 14, 15):
        poll_status = '참여 가능'
    elif c_now_int in (20, 30):
        poll_status = '참여 불가'
    else:
        poll_status = '답변 대기'
    title_raw = result_data.get('C_A_TITLE', '')
    title_text = title_raw.split(')', 1)[-1].strip() if ')' in title_raw else title_raw.strip()
    title_fields = {
        '상태': process_status,
        '신고번호': result_data.get('STTEMNT_NO', ''),
        '신고명': title_text,
        '신고일': result_data.get('C_DATE', ''),
        '만족도조사여부': poll_status,
    }

    return {
        "entry_value": entry_value,
        "car_number": car_number,
        "occurrence_date": occurrence_date,
        "occurrence_time": occurrence_time,
        "violation_location": violation_location,
        "progress_status": process_status,
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
        "title_fields": title_fields,
    }
