import pandas as pd
from time import sleep
import settings.settings as settings
from core.utils import logger
import services.parser as doc_parser

def crawl_details(driver, list):
    """Crawls the detail page for each report link using API."""
    import json
    
    if "safetyreport.go.kr" not in driver.current_url:
        driver.get(settings.myreporturl)
        sleep(2)
        
    for link in list:
        logger.LoggerFactory.logbot.debug(f"[API] Fetching details for ID: {link}")
        
        script = f"""
        var callback = arguments[arguments.length - 1];
        $.get('/api/v1/portal/mypage/mysafereport/{str(link)}')
         .done(function(data) {{ callback(data); }})
         .fail(function(jqXHR, textStatus, errorThrown) {{ callback({{error: textStatus + " " + errorThrown}}); }});
        """
        
        try:
            data = driver.execute_async_script(script)
            
            if "error" in data or "result" not in data:
                logger.LoggerFactory.logbot.error(f"Error fetching JSON API for {link}: {data.get('error', 'No result key')}")
                continue
                
            result_data = data["result"]
            
            details = doc_parser.parse_json_details(result_data)
            
            cols = ["ID", "처리상태", "차량번호", "위반법규", "범칙금_과태료", "벌점", "처리기관", "담당자", "답변일", "발생일자", "발생시각", "위반장소", "종결여부", "신고내용", "처리내용", "지도", "첨부사진", "첨부파일"]
            
            detaillist = [
                link,
                details["processing_status"],
                details["car_number"],
                details["violation_law"],
                details["penalty_amount"],
                details["penalty_points"],
                details["processing_agency"],
                details["person_in_charge"],
                details["response_date"],
                details["occurrence_date"],
                details["occurrence_time"],
                details["violation_location"],
                details["processing_finish"],
                details["report_content"],
                details["processing_content"],
                details["map_image"],
                details["attached_photos"],
                details["attachment_files"],
            ]
            
            logger.LoggerFactory.logbot.info(str(detaillist))
            df = pd.DataFrame([detaillist], columns=cols)
            entry_value = details.get("entry_value", "")
            from core.database.database import category_from_entry_value
            category = category_from_entry_value(entry_value)
            yield (df, category, entry_value)
            sleep(0.3)
            
        except Exception as e:
            logger.LoggerFactory.logbot.error(f"Error processing link {link} via API: {e}")
            continue
