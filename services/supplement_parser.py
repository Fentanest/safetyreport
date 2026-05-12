"""보완요청 이력 파서.

`/portal/mypage/mySafeReportView` 페이지의 `splmntDivBody` 영역에는
1 round 당 1 `<table>` 형태로 보완 이력이 들어 있다. round 수는 다회차일 수 있고,
마지막 round 가 완료일시 비어 있으면 현재 열린(open) 보완 요청이다.

API JSON 응답에는 일반적으로 "직전 완료 round" + "현재 round" 일부 필드만 포함되어
전체 이력 복원이 불가능하므로, 보완 흔적이 있는 신고는 같은 HTML 페이지를 추가로 가져와
이 모듈로 round 리스트를 정확히 만들어 사용한다.
"""
from __future__ import annotations

import re
from typing import Iterable

from bs4 import BeautifulSoup


_FIELD_KEYS = (
    "보완 요청자",
    "보완 요청자 연락처",
    "보완 요청 일시",
    "보완 완료 일시",
    "보완 요청 내용",
    "신고자 보완 의견",
    "신고자 보완 첨부파일",
)


def _extract_th_td_pairs(table) -> dict[str, object]:
    """table 안의 모든 th/td 쌍을 dict 로 만든다. th 가 비어 있으면 무시."""
    pairs: dict[str, object] = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if not cells:
            cells = row.find_all(["th", "td"])
        idx = 0
        while idx < len(cells) - 1:
            th = cells[idx]
            td = cells[idx + 1]
            if th.name == "th" and td.name == "td":
                key = th.get_text(" ", strip=True)
                if key:
                    pairs[key] = td
                idx += 2
            else:
                idx += 1
    return pairs


def _td_text(td) -> str:
    if td is None:
        return ""
    return td.get_text("\n", strip=True)


def _td_text_with_br(td) -> str:
    """<br> 를 줄바꿈으로 보존해서 텍스트화 (신고자 보완 의견에 필요)."""
    if td is None:
        return ""
    for br in td.find_all("br"):
        br.replace_with("\n")
    return td.get_text(" ", strip=True).replace(" \n", "\n").replace("\n ", "\n").strip()


def _extract_attachment_urls(td) -> tuple[str, str]:
    """신고자 보완 첨부파일 td 에서 URL 두 종(첨부파일, 지도) 추출.

    안전신문고는 보통 data-title="/fileDown/..." + <img src="/fileDown/...MAPIMG.png"> 패턴.
    """
    if td is None:
        return "", ""
    files: list[str] = []
    map_urls: list[str] = []

    def _abs(url: str) -> str:
        if not url:
            return ""
        if url.startswith("/"):
            return f"https://www.safetyreport.go.kr{url}"
        return url

    seen: set[str] = set()
    for tag in td.find_all(["a", "img", "video", "source"]):
        candidate = (
            tag.get("data-title")
            or tag.get("href")
            or tag.get("src")
            or ""
        )
        if not candidate or not candidate.startswith("/fileDown"):
            continue
        absolute = _abs(candidate)
        if absolute in seen:
            continue
        seen.add(absolute)
        if "MAPIMG" in absolute:
            map_urls.append(absolute)
        else:
            files.append(absolute)
    return "\n".join(files), "\n".join(map_urls)


_OPINION_PATTERNS = {
    "신고자_보완_차량번호": re.compile(r"차량번호\s*[:：]\s*(.+?)(?=\n|\*|$)"),
    "신고자_보완_발생일자": re.compile(r"발생일자\s*[:：]\s*(\d{4})[.\-](\d{1,2})[.\-](\d{1,2})\.?"),
    "신고자_보완_발생시각": re.compile(r"발생시각\s*[:：]\s*(\d{2}:\d{2})"),
    "신고자_보완_위반장소": re.compile(r"위반장소\s*[:：]\s*(.+?)(?=\n|\*|$)"),
}


def _parse_opinion_overrides(opinion_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    if not opinion_text:
        return result
    for field, pattern in _OPINION_PATTERNS.items():
        match = pattern.search(opinion_text)
        if not match:
            continue
        if field == "신고자_보완_차량번호":
            result[field] = re.sub(r"\s+", "", match.group(1))
        elif field == "신고자_보완_발생일자":
            result[field] = f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        elif field == "신고자_보완_발생시각":
            result[field] = match.group(1)
        else:
            result[field] = match.group(1).strip()
    return result


def parse_supplement_rounds_from_html(page_html_or_soup, *, source_type: str) -> list[dict]:
    """splmntDivBody 안의 모든 보완 round 를 추출해 list 로 돌려준다.

    source_type 는 history row 에 함께 저장되는 출처 라벨 ('legacy_html', 'api_html_supp', ...).
    각 round dict 키 — _SUPPLEMENT_HISTORY_FIELDS 와 같은 한글 키 + round_no/is_open.
    """
    if not page_html_or_soup:
        return []

    if isinstance(page_html_or_soup, BeautifulSoup):
        soup = page_html_or_soup
    else:
        soup = BeautifulSoup(page_html_or_soup, "html.parser")

    body = soup.find("div", id="splmntDivBody")
    if not body:
        return []

    rounds: list[dict] = []
    tables = body.find_all("table")
    for index, table in enumerate(tables, start=1):
        pairs = _extract_th_td_pairs(table)
        if not pairs:
            continue

        requester = _td_text(pairs.get("보완 요청자"))
        requester_tel = _td_text(pairs.get("보완 요청자 연락처"))
        requested_at = _td_text(pairs.get("보완 요청 일시"))
        completed_at = _td_text(pairs.get("보완 완료 일시"))
        request_content = _td_text_with_br(pairs.get("보완 요청 내용"))
        opinion = _td_text_with_br(pairs.get("신고자 보완 의견"))
        attached_files, attached_map = _extract_attachment_urls(pairs.get("신고자 보완 첨부파일"))

        # round 가 형식상 비어 있으면 skip (예: 헤더 정도만 들어간 빈 표)
        if not any([requester, requester_tel, requested_at, request_content, opinion]):
            continue

        is_open = "Y" if not completed_at else "N"

        round_data = {
            "round_no": index,
            "보완_요청자": requester,
            "보완_요청자_연락처": requester_tel,
            "보완_요청_일시": requested_at,
            "보완_완료_일시": completed_at,
            "보완_요청_내용": request_content,
            "신고자_보완_의견": opinion,
            "신고자_보완_첨부파일": attached_files,
            "신고자_보완_지도": attached_map,
            "is_open": is_open,
            "source_type": source_type,
        }
        round_data.update(_parse_opinion_overrides(opinion))
        rounds.append(round_data)

    return rounds


def latest_completed_overrides(rounds: Iterable[dict] | None) -> dict[str, str]:
    """완료된 마지막 round 가 가진 신고자 보완 의견 기준 override 값 반환.

    이전 로직과 동일하게 마지막 완료 보완 round 가 신고 행의 차량번호/발생일자/발생시각/위반장소 를 덮어쓴다.
    """
    if not rounds:
        return {}
    last_completed = None
    for round_data in rounds:
        if round_data.get("is_open") == "Y":
            continue
        last_completed = round_data
    if not last_completed:
        return {}
    override = {}
    if last_completed.get("신고자_보완_차량번호"):
        override["car_number"] = last_completed["신고자_보완_차량번호"]
    if last_completed.get("신고자_보완_발생일자"):
        override["occurrence_date"] = last_completed["신고자_보완_발생일자"]
    if last_completed.get("신고자_보완_발생시각"):
        override["occurrence_time"] = last_completed["신고자_보완_발생시각"]
    if last_completed.get("신고자_보완_위반장소"):
        override["violation_location"] = last_completed["신고자_보완_위반장소"]
    return override


def has_open_round(rounds: Iterable[dict] | None) -> bool:
    return any((r.get("is_open") == "Y") for r in (rounds or []))
