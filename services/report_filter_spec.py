"""신고 필터 사양(EO R-02). 필터 키·기본값·일치 방식과 단계별 책임을 한곳에 둔다.

단계
- `sql_candidates(table)`: DB 에서 후보를 줄이는 조건. 최종 결과의 **상위 집합**이어야 한다(놓치면 안 됨).
  확신할 수 없는 조건(대소문자·기관 표시명이 바뀌는 경우 등)은 넣지 않는다.
- `apply_rows(df)` / `apply_law(df)`: pandas 최종 판정. `matches_row` / `matches_law`(순수 판정)와 같은 결과다.
  기관 조건은 registry 표시명(`_apply_registry_agency_display` 뒤의 처리기관)에 건다.
- 법규 조건은 '법규 선택지'를 만든 뒤에 거는 별도 단계라 따로 둔다.

같은 의미를 웹 목록(web/static/ui/list-predicates.js)과 모바일 목록(lib/services/report_query.dart)이 쓰며,
`contracts/report-filter-vectors.json`(두 레포 바이트 동일)으로 함께 검사한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd
from sqlalchemy import func, or_

from services import report_policy

EMPTY_LAW = "__없음__"
POLICE_TEXT = "경찰"

# (필터 키 접두어, 열, 비교 자릿수, 시각 여부)
RANGE_FIELDS = (("reportDate", "신고일", 10, False), ("occurDate", "발생일자", 10, False),
                ("responseDate", "답변일", 10, False), ("occurTime", "발생시각", 5, True))
# (필터 키, 열): AND(&)·OR(,) 검색
TEXT_FIELDS = (("reportName", "신고명"), ("location", "위반장소"), ("agency", "처리기관"))

_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})(?:$|[ T])")
_TIME = re.compile(r"^(\d{2}):(\d{2})(?::\d{2})?")


def parse_groups(query) -> tuple[tuple[str, ...], ...]:
    """'a&b,c' → ((a, b), (c,)). 빈 항목은 버리고, 남는 게 없으면 ()(조건 없음)."""
    text = report_policy.norm(query)
    groups = []
    for raw_group in text.split(","):
        terms = tuple(report_policy.norm(term).casefold() for term in raw_group.split("&"))
        terms = tuple(term for term in terms if term)
        if terms:
            groups.append(terms)
    return tuple(groups)


def range_key(value, time: bool) -> str | None:
    """비교용 앞자리. 형식이 맞지 않으면 None(범위 조건이 있으면 제외)."""
    text = "" if value is None or (not isinstance(value, str) and pd.isna(value)) else str(value)
    if time:
        match = _TIME.match(text)
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            return None
        return f"{match.group(1)}:{match.group(2)}"
    match = _DATE.match(text)
    if not match or not _valid_ymd(int(match.group(1)), int(match.group(2)), int(match.group(3))):
        return None
    return text[:10]


def _valid_ymd(year: int, month: int, day: int) -> bool:
    """그레고리력 날짜인지. 0000년도 받는다(JS Date·SQLite date() 와 같게 — Python date 는 1년부터라 직접 센다)."""
    if not 1 <= month <= 12 or day < 1:
        return False
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    days = (31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31)[month - 1]
    return day <= days


@dataclass(frozen=True)
class RangeFilter:
    column: str
    width: int
    time: bool
    start: str
    end: str

    def matches(self, value) -> bool:
        key = range_key(value, self.time)
        if key is None:
            return False
        return (not self.start or self.start <= key) and (not self.end or key <= self.end)


@dataclass(frozen=True)
class TextFilter:
    column: str
    groups: tuple[tuple[str, ...], ...]
    exact: bool = False

    def matches(self, value) -> bool:
        if not self.groups:
            return True
        source = report_policy.norm(value).casefold()
        if self.exact:
            return source == self.groups[0][0]
        return any(all(term in source for term in group) for group in self.groups)

    def sql_safe_term(self) -> str | None:
        """SQL 후보 축소에 쓸 수 있는 단일 검색어. SQLite LIKE 는 ASCII 만 대소문자를 무시하므로
        그 밖의 대소문자 글자가 있으면 넣지 않는다(후보를 놓치지 않게)."""
        if self.exact or len(self.groups) != 1 or len(self.groups[0]) != 1:
            return None
        term = self.groups[0][0]
        if any(ord(ch) >= 128 and ch.lower() != ch.upper() for ch in term):
            return None
        return term


@dataclass(frozen=True)
class ReportFilterSpec:
    year: str = ""
    ranges: tuple[RangeFilter, ...] = ()
    texts: tuple[TextFilter, ...] = ()
    police: str = ""  # "" | "exclude" | "only"
    law: str = ""

    @classmethod
    def from_filters(cls, filters) -> "ReportFilterSpec":
        filters = filters or {}
        year = str(filters.get("year") or "")
        ranges = []
        for prefix, column, width, time in RANGE_FIELDS:
            start, end = str(filters.get(prefix + "Start") or ""), str(filters.get(prefix + "End") or "")
            if start or end:
                ranges.append(RangeFilter(column, width, time, start, end))
        texts = []
        for key, column in TEXT_FIELDS:
            groups = parse_groups(filters.get(key))
            if not groups:
                continue
            raw = str(filters.get(key) or "")
            exact = (key == "agency" and bool(filters.get("agencyExact")) and "&" not in raw and "," not in raw
                     and len(groups) == 1 and len(groups[0]) == 1)
            texts.append(TextFilter(column, groups, exact))
        police = "exclude" if filters.get("excludePolice") else ("only" if filters.get("onlyPolice") else "")
        if filters.get("excludePolice") and filters.get("onlyPolice"):
            police = "both"  # 둘 다 켜면 아무것도 남지 않는다(기존 동작 그대로)
        return cls("" if year in ("all",) else year, tuple(ranges), tuple(texts), police, str(filters.get("law") or ""))

    # ── 순수 판정 ────────────────────────────────────────────────────────
    def matches_row(self, row) -> bool:
        if self.year and not str(row.get("답변일") or "").startswith(self.year):
            return False
        if not all(r.matches(row.get(r.column)) for r in self.ranges):
            return False
        if not all(t.matches(row.get(t.column)) for t in self.texts):
            return False
        police = POLICE_TEXT in str(row.get("처리기관") or "")
        if self.police in ("exclude", "both") and police:
            return False
        if self.police in ("only", "both") and not police:
            return False
        return True

    def matches_law(self, row) -> bool:
        if not self.law:
            return True
        value = report_policy.norm(row.get("위반법규"))
        return value == "" if self.law == EMPTY_LAW else value == report_policy.norm(self.law)

    # ── pandas 최종 판정 ─────────────────────────────────────────────────
    def apply_rows(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        mask = pd.Series(True, index=df.index)
        if self.year and "답변일" in df:
            mask &= df["답변일"].fillna("").astype(str).str.startswith(self.year)
        for r in self.ranges:
            if r.column in df:
                mask &= df[r.column].map(r.matches).astype(bool)
        for t in self.texts:
            if t.column in df:
                mask &= df[t.column].map(t.matches).astype(bool)
        if self.police and "처리기관" in df:
            police = df["처리기관"].fillna("").astype(str).str.contains(POLICE_TEXT, regex=False)
            if self.police in ("exclude", "both"):
                mask &= ~police
            if self.police in ("only", "both"):
                mask &= police
        return df[mask]

    def apply_law(self, df: pd.DataFrame) -> pd.DataFrame:
        if not self.law or df.empty or "위반법규" not in df:
            return df
        value = report_policy.text_series(df, "위반법규")
        if self.law == EMPTY_LAW:
            return df[value == ""]
        return df[value == report_policy.norm(self.law)]

    # ── SQL 후보 축소(상위 집합) ─────────────────────────────────────────
    def sql_candidates(self, table) -> list:
        clauses = []
        if self.year and "답변일" in table.c:
            clauses.append(table.c["답변일"].startswith(self.year))
        for r in self.ranges:
            if r.column not in table.c:
                continue
            column = table.c[r.column]
            value = func.substr(column, 1, r.width)
            if r.start:
                clauses.append(value >= r.start)
                # 같은 범위를 원래 열 비교로 한 번 더 건다 — 열 인덱스로 범위를 좁힌다(기술일지 B-05). 결과 행은 같다.
                if not r.time and len(r.start) == 10:
                    clauses.append(column >= r.start)
            if r.end:
                clauses.append(value <= r.end)
                clauses.append(func.length(value) == r.width)
                if not r.time and len(r.end) == 10:
                    clauses.append(column < r.end + "\U0010ffff")
        for t in self.texts:
            term = t.sql_safe_term()
            # 기관은 registry 표시명으로 최종 판정하므로 원문 열로 줄이지 않는다.
            if term and t.column != "처리기관" and t.column in table.c:
                clauses.append(table.c[t.column].contains(term))
        if self.police and "처리기관" in table.c:
            # 코드가 있으면 registry 가 표시명을 바꿀 수 있어 후보로 남긴다(최종 판정은 표시명).
            raw = func.coalesce(table.c["처리기관"], "").contains(POLICE_TEXT)
            has_code = func.coalesce(table.c["처리기관코드"], "") != "" if "처리기관코드" in table.c else None
            if self.police == "exclude":
                clauses.append(or_(~raw, has_code) if has_code is not None else ~raw)
            elif self.police == "only":
                clauses.append(or_(raw, has_code) if has_code is not None else raw)
        return clauses
