# -*- coding: utf-8 -*-
import csv
import time
from collections import defaultdict
from datetime import datetime

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

BASE_URL = "https://www.safetyreport.go.kr/api/v1/portal/introduction/safeSingoStatistics"

CATEGORY_GROUPS = [
    {
        "name": "불법주정차신고",
        "children": [
            "소화전",
            "교차로 모퉁이",
            "버스정류소",
            "횡단보도",
            "어린이보호구역",
            "인도",
            "장애인전용구역",
            "기타불법주정차",
            "소방차전용구역",
            "친환경차충전구역",
        ],
    },
    {
        "name": "자동차·교통위반",
        "children": [
            "자동차 안전신고",
            "교통위반(고속도로 포함)",
            "이륜차 위반",
            "(구)적재물 추락방지,중량·용량 위반",
            "버스전용차로 위반(고속도로제외)",
            "번호판 규정 위반",
            "불법등화, 반사판(지) 가림·손상",
            "불법 튜닝, 해체, 조작",
            "기타 자동차 안전기준 위반",
            "난폭/보복운전",
        ],
    },
]

TARGET_CATEGORIES = [
    child
    for group in CATEGORY_GROUPS
    for child in group["children"]
]

CATEGORY_LOOKUP = {
    child: group["name"]
    for group in CATEGORY_GROUPS
    for child in group["children"]
}

REGIONS = {
    "서울특별시": {
        "sido": "6110000",
        "sigungu": {
            "종로구": "3000000", "중구": "3010000", "용산구": "3020000", "성동구": "3030000",
            "광진구": "3040000", "동대문구": "3050000", "중랑구": "3060000", "성북구": "3070000",
            "강북구": "3080000", "도봉구": "3090000", "노원구": "3100000", "은평구": "3110000",
            "서대문구": "3120000", "마포구": "3130000", "양천구": "3140000", "강서구": "3150000",
            "구로구": "3160000", "금천구": "3170000", "영등포구": "3180000", "동작구": "3190000",
            "관악구": "3200000", "서초구": "3210000", "강남구": "3220000", "송파구": "3230000",
            "강동구": "3240000",
        },
    },
    "부산광역시": {
        "sido": "6260000",
        "sigungu": {
            "중구": "3250000", "서구": "3260000", "동구": "3270000", "영도구": "3280000",
            "부산진구": "3290000", "동래구": "3300000", "남구": "3310000", "북구": "3320000",
            "해운대구": "3330000", "사하구": "3340000", "금정구": "3350000", "강서구": "3360000",
            "연제구": "3370000", "수영구": "3380000", "사상구": "3390000", "기장군": "3400000",
        },
    },
    "대구광역시": {
        "sido": "6270000",
        "sigungu": {
            "중구": "3410000", "동구": "3420000", "서구": "3430000", "남구": "3440000",
            "북구": "3450000", "수성구": "3460000", "달서구": "3470000",
            "달성군": "3480000", "군위군": "5141000",
        },
    },
    "인천광역시": {
        "sido": "6280000",
        "sigungu": {
            "중구": "3490000", "동구": "3500000", "미추홀구": "3510000", "연수구": "3520000",
            "남동구": "3530000", "부평구": "3540000", "계양구": "3550000", "서구": "3560000",
            "강화군": "3570000", "옹진군": "3580000",
        },
    },
    "광주광역시": {
        "sido": "6290000",
        "sigungu": {
            "동구": "3590000", "서구": "3600000", "남구": "3610000", "북구": "3620000",
            "광산구": "3630000",
        },
    },
    "대전광역시": {
        "sido": "6300000",
        "sigungu": {
            "동구": "3640000", "중구": "3650000", "서구": "3660000", "유성구": "3670000",
            "대덕구": "3680000",
        },
    },
    "울산광역시": {
        "sido": "6310000",
        "sigungu": {
            "중구": "3690000", "남구": "3700000", "동구": "3710000", "북구": "3720000",
            "울주군": "3730000",
        },
    },
    "경기도": {
        "sido": "6410000",
        "sigungu": {
            "수원시": "3740000",
            "성남시": "3780000",
            "의정부시": "3820000",
            "안양시": "3830000",
            "부천시": "3860000",
            "광명시": "3900000",
            "평택시": "3910000",
            "동두천시": "3920000",
            "안산시": "3930000",
            "고양시": "3940000",
            "과천시": "3970000",
            "구리시": "3980000",
            "남양주시": "3990000",
            "오산시": "4000000",
            "시흥시": "4010000",
            "군포시": "4020000",
            "의왕시": "4030000",
            "하남시": "4040000",
            "용인시": "4050000",
            "파주시": "4060000",
            "이천시": "4070000",
            "안성시": "4080000",
            "김포시": "4090000",
            "연천군": "4140000",
            "가평군": "4160000",
            "양평군": "4170000",
            "화성시": "5530000",
            "광주시": "5540000",
            "양주시": "5590000",
            "포천시": "5600000",
        },
    },
    "강원특별자치도": {
        "sido": "6530000",
        "sigungu": {
            "춘천시": "4181000", "원주시": "4191000", "강릉시": "4201000", "동해시": "4211000",
            "태백시": "4221000", "속초시": "4231000", "삼척시": "4241000", "홍천군": "4251000",
            "횡성군": "4261000", "영월군": "4271000", "평창군": "4281000", "정선군": "4291000",
            "철원군": "4301000", "화천군": "4311000", "양구군": "4321000", "인제군": "4331000",
            "고성군": "4341000", "양양군": "4351000",
        },
    },
    "충청북도": {
        "sido": "6430000",
        "sigungu": {
            "청주시": "5710000", "충주시": "4390000", "제천시": "4400000", "보은군": "4420000",
            "옥천군": "4430000", "영동군": "4440000", "진천군": "4450000", "괴산군": "4460000",
            "음성군": "4470000", "단양군": "4480000", "증평군": "5570000",
        },
    },
    "충청남도": {
        "sido": "6440000",
        "sigungu": {
            "천안시": "4490000", "공주시": "4500000", "보령시": "4510000", "아산시": "4520000",
            "서산시": "4530000", "논산시": "4540000", "금산군": "4550000", "부여군": "4570000",
            "서천군": "4580000", "청양군": "4590000", "홍성군": "4600000", "예산군": "4610000",
            "태안군": "4620000", "계룡시": "5580000", "당진시": "5680000",
        },
    },
    "전북특별자치도": {
        "sido": "6540000",
        "sigungu": {
            "전주시": "4641000", "군산시": "4671000", "익산시": "4681000", "정읍시": "4691000",
            "남원시": "4701000", "김제시": "4711000", "완주군": "4721000", "진안군": "4731000",
            "무주군": "4741000", "장수군": "4751000", "임실군": "4761000", "순창군": "4771000",
            "고창군": "4781000", "부안군": "4791000",
        },
    },
    "전라남도": {
        "sido": "6460000",
        "sigungu": {
            "목포시": "4800000", "여수시": "4810000", "순천시": "4820000", "나주시": "4830000",
            "광양시": "4840000", "담양군": "4850000", "곡성군": "4860000", "구례군": "4870000",
            "고흥군": "4880000", "보성군": "4890000", "화순군": "4900000", "장흥군": "4910000",
            "강진군": "4920000", "해남군": "4930000", "영암군": "4940000", "무안군": "4950000",
            "함평군": "4960000", "영광군": "4970000", "장성군": "4980000", "완도군": "4990000",
            "진도군": "5000000", "신안군": "5010000",
        },
    },
    "경상북도": {
        "sido": "6470000",
        "sigungu": {
            "포항시": "5020000", "경주시": "5050000", "김천시": "5060000", "안동시": "5070000",
            "구미시": "5080000", "영주시": "5090000", "영천시": "5100000", "상주시": "5110000",
            "문경시": "5120000", "경산시": "5130000", "의성군": "5150000", "청송군": "5160000",
            "영양군": "5170000", "영덕군": "5180000", "청도군": "5190000", "고령군": "5200000",
            "성주군": "5210000", "칠곡군": "5220000", "예천군": "5230000", "봉화군": "5240000",
            "울진군": "5250000", "울릉군": "5260000",
        },
    },
    "경상남도": {
        "sido": "6480000",
        "sigungu": {
            "창원시": "5670000", "진주시": "5310000", "통영시": "5330000", "사천시": "5340000",
            "김해시": "5350000", "밀양시": "5360000", "거제시": "5370000", "양산시": "5380000",
            "의령군": "5390000", "함안군": "5400000", "창녕군": "5410000", "고성군": "5420000",
            "남해군": "5430000", "하동군": "5440000", "산청군": "5450000", "함양군": "5460000",
            "거창군": "5470000", "합천군": "5480000",
        },
    },
    "제주특별자치도": {
        "sido": "6500000",
        "sigungu": {
            "제주시": "6510000", "서귀포시": "6520000",
        },
    },
    "세종특별자치시": {
        "sido": "5690000",
        "sigungu": {
            "조치원읍": "5690066", "연기면": "5690067", "연동면": "5690068", "부강면": "5690069",
            "금남면": "5690070", "장군면": "5690071", "연서면": "5690072", "전의면": "5690073",
            "전동면": "5690074", "소정면": "5690075", "한솔동": "5690076", "도담동": "5690123",
            "아름동": "5690145", "종촌동": "5690184", "고운동": "5690219", "보람동": "5690220",
            "새롬동": "5690232", "대평동": "5690243", "소담동": "5690244", "다정동": "5690325",
            "반곡동": "5690351", "해밀동": "5690352", "어진동": "5690425", "나성동": "5690426",
        },
    },
}


def get_target_yyyymm(now=None):
    if now is None:
        now = datetime.now()
    return now.strftime("%Y%m")


def build_common_params(target_yyyymm=None):
    target_yyyymm = target_yyyymm or get_target_yyyymm()
    return {
        "searchYesterday": "",
        "seachDateType": "A",
        "C_FRM_YM": target_yyyymm,
        "C_TO_YM": target_yyyymm,
    }


def format_period_label(target_yyyymm):
    if len(str(target_yyyymm)) != 6:
        return str(target_yyyymm)
    return f"{str(target_yyyymm)[:4]}-{str(target_yyyymm)[4:6]}"

def build_session():
    session = requests.Session()

    retry_strategy = Retry(
        total=8,
        connect=8,
        read=8,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )

    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.safetyreport.go.kr/",
        "Connection": "close",
    })
    return session

def fetch_stats(session, sido_code, sigungu_code, target_yyyymm=None, logger_fn=None, max_attempts=6):
    params = build_common_params(target_yyyymm)
    params["API_CTRD_CODE"] = sido_code
    params["API_SIGNGU_CODE"] = sigungu_code

    last_error = None
    logger_fn = logger_fn or print

    for attempt in range(max_attempts):
        try:
            resp = session.get(BASE_URL, params=params, timeout=(10, 30))
            resp.raise_for_status()
            data = resp.json()
            return data.get("result", [])
        except Exception as e:
            last_error = e
            wait_sec = min(2 + attempt, 10)
            logger_fn(f"  [재시도 {attempt + 1}/{max_attempts}] {sido_code}-{sigungu_code} 실패: {e} / {wait_sec}초 대기")
            time.sleep(wait_sec)

    raise last_error

def normalize_item_name(item):
    for key in ["NM", "NAME", "SUB_NM", "TITLE", "CD_NM"]:
        value = item.get(key)
        if value:
            return str(value).strip()
    return ""

def extract_count(item):
    try:
        return int(item.get("CNT", 0) or 0)
    except Exception:
        return 0


def get_parent_category_name(subcategory):
    return CATEGORY_LOOKUP.get(subcategory, "")


def get_category_label(subcategory):
    parent = get_parent_category_name(subcategory)
    if not parent:
        return subcategory
    return f"{parent} > {subcategory}"

def save_all_rows_csv(rows, filename):
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["카테고리", "대분류", "소분류", "시도", "시군구", "건수"]
        )
        writer.writeheader()
        writer.writerows(rows)

def save_top5_csv(top5_rows, filename):
    with open(filename, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["카테고리", "대분류", "소분류", "순위", "시도", "시군구", "건수"]
        )
        writer.writeheader()
        writer.writerows(top5_rows)


def build_top5_rows(category_ranking):
    top5_csv_rows = []
    top5_by_category = []

    for group in CATEGORY_GROUPS:
        group_entry = {
            "name": group["name"],
            "children": [],
        }

        for subcategory in group["children"]:
            rows = sorted(category_ranking[subcategory], key=lambda x: x["건수"], reverse=True)[:5]
            category_items = []
            for idx, row in enumerate(rows, 1):
                item = {
                    "rank": idx,
                    "sido": row["시도"],
                    "sigungu": row["시군구"],
                    "count": row["건수"],
                    "region": f"{row['시도']} {row['시군구']}",
                }
                category_items.append(item)
                top5_csv_rows.append({
                    "카테고리": get_category_label(subcategory),
                    "대분류": group["name"],
                    "소분류": subcategory,
                    "순위": idx,
                    "시도": row["시도"],
                    "시군구": row["시군구"],
                    "건수": row["건수"],
                })

            group_entry["children"].append({
                "name": subcategory,
                "full_name": get_category_label(subcategory),
                "items": category_items,
            })

        top5_by_category.append(group_entry)

    return top5_csv_rows, top5_by_category


def collect_statistics(target_yyyymm=None, logger_fn=None, retry_failed_passes=1):
    target_yyyymm = target_yyyymm or get_target_yyyymm()
    logger_fn = logger_fn or print
    session = build_session()

    category_ranking = defaultdict(list)
    all_rows = []
    failed = []

    logger_fn(f"=== 안전신문고 통계 수집 시작 ({format_period_label(target_yyyymm)}) ===")

    for sido_name, info in REGIONS.items():
        sido_code = info["sido"]

        for sigungu_name, sigungu_code in info["sigungu"].items():
            try:
                result_items = fetch_stats(
                    session,
                    sido_code,
                    sigungu_code,
                    target_yyyymm=target_yyyymm,
                    logger_fn=logger_fn,
                )

                row_map = {}
                for item in result_items:
                    name = normalize_item_name(item)
                    cnt = extract_count(item)
                    if name in TARGET_CATEGORIES:
                        row_map[name] = cnt

                for category in TARGET_CATEGORIES:
                    cnt = row_map.get(category, 0)
                    parent = get_parent_category_name(category)
                    category_ranking[category].append({
                        "시도": sido_name,
                        "시군구": sigungu_name,
                        "건수": cnt,
                    })
                    all_rows.append({
                        "카테고리": get_category_label(category),
                        "대분류": parent,
                        "소분류": category,
                        "시도": sido_name,
                        "시군구": sigungu_name,
                        "건수": cnt,
                    })

                logger_fn(f"[완료] {sido_name} {sigungu_name}")
            except Exception as e:
                logger_fn(f"[에러] {sido_name} {sigungu_name}: {e}")
                failed.append({
                    "시도": sido_name,
                    "시도코드": sido_code,
                    "시군구": sigungu_name,
                    "시군구코드": sigungu_code,
                })

    for retry_index in range(retry_failed_passes):
        if not failed:
            break

        logger_fn(f"=== 실패 지역 재수집 시작 ({retry_index + 1}/{retry_failed_passes}) ===")
        retry_failed = []

        for item in failed:
            try:
                result_items = fetch_stats(
                    session,
                    item["시도코드"],
                    item["시군구코드"],
                    target_yyyymm=target_yyyymm,
                    logger_fn=logger_fn,
                )

                row_map = {}
                for resp_item in result_items:
                    name = normalize_item_name(resp_item)
                    cnt = extract_count(resp_item)
                    if name in TARGET_CATEGORIES:
                        row_map[name] = cnt

                for category in TARGET_CATEGORIES:
                    cnt = row_map.get(category, 0)
                    parent = get_parent_category_name(category)
                    category_ranking[category].append({
                        "시도": item["시도"],
                        "시군구": item["시군구"],
                        "건수": cnt,
                    })
                    all_rows.append({
                        "카테고리": get_category_label(category),
                        "대분류": parent,
                        "소분류": category,
                        "시도": item["시도"],
                        "시군구": item["시군구"],
                        "건수": cnt,
                    })

                logger_fn(f"[재수집 완료] {item['시도']} {item['시군구']}")
            except Exception as e:
                logger_fn(f"[재수집 실패] {item['시도']} {item['시군구']}: {e}")
                retry_failed.append(item)

        failed = retry_failed

    top5_csv_rows, top5_by_category = build_top5_rows(category_ranking)
    return {
        "period": target_yyyymm,
        "period_label": format_period_label(target_yyyymm),
        "all_rows": all_rows,
        "top5_rows": top5_csv_rows,
        "top5_by_category": top5_by_category,
        "failed": failed,
    }

def main():
    result = collect_statistics()
    period = result["period"]
    all_csv = f"safetyreport_category_all_{period}.csv"
    top5_csv = f"safetyreport_category_top5_{period}.csv"

    print("\n=== 항목별 전국 TOP 5 ===\n")
    for group_info in result["top5_by_category"]:
        print(f"[{group_info['name']}]")
        for category_info in group_info["children"]:
            print(f"  - {category_info['name']}")
            for item in category_info["items"]:
                print(f"    {item['rank']}위: {item['region']} - {item['count']}건")
            print()

    save_all_rows_csv(result["all_rows"], all_csv)
    save_top5_csv(result["top5_rows"], top5_csv)

    print("=== 저장 완료 ===")
    print(f"전체 데이터 CSV: {all_csv}")
    print(f"TOP5 데이터 CSV: {top5_csv}")

    if result["failed"]:
        print("\n=== 끝까지 실패한 지역 ===")
        for item in result["failed"]:
            print(f"{item['시도']} {item['시군구']} ({item['시도코드']}, {item['시군구코드']})")
    else:
        print("\n모든 지역 수집 완료")

if __name__ == "__main__":
    main()
