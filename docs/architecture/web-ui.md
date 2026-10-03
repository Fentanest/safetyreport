# 웹UI 동작 규칙

다루는 것: 대시보드 카드 URL, 사이드바/세션/프록시, 통계 탭·상세검색·agencyExact, 첨부 인라인 미디어.

2026-09-24 에 기존 루트 `CLAUDE.md`(725줄)에서 옮겼다. 아래 '이관 원문' 은 문구를 바꾸지 않고 옮긴 것이며, 원본 전체는 [legacy-claude-reference.md](legacy-claude-reference.md)에 있다.

## 코드 대조 정정 (기준 17df6cb)

| 원문 절 | 현재 코드 | 조치 |
|---|---|---|
| 웹 대시보드 카드 → 상세 URL | `index.html` 에 `/data/parking` 로 가는 '주정차위반' 카드는 없다(카드 12개: 총 신고/보완 요청/처리 중/답변 완료/취하/수용/일부수용/불수용·기타 + 교통 과태료/경고·범칙금/교통 불수용/미확인). | 정정 |
| 웹 대시보드 카드 → 상세 URL | `?status=`, `?fine=`, `?agencyExact=` 는 `data_table.html` JS 가 아니라 서버(`services/report_query_service.py:57-80,115-119`)가 적용한다. JS 는 `agency/person/car/law/location/open` 만 읽는다. | 정정 |
| 웹 통계 탭 구조 | 위반법규는 탭이 아니라 오른쪽 사이드바 버튼(`#statsLawSidebar`, `?law=`)이다. 라우터가 `records_*_law` 를 넘기지만 템플릿은 렌더하지 않는다. 차트 라이브러리는 없다(표만 있음). | 정정 |
| 웹 통계 탭 구조 (2026-09-28) | 통계 화면 개편: 법규는 검색 가능한 선택창, 행 클릭은 선택 항목 상세(목록 이동은 상세의 버튼), 요약 카드 6개·지도·차트 추가, 전국 안전신고 현황(Sunwi)은 대시보드에서 통계 하단으로. 사이드바 이름 '통계'. 상세는 [statistics-spec §9](../design/statistics-spec.md) | 정정 |
| 공통 | DataTables 한국어 파일을 `//cdn.datatables.net/...` 로 불러 http 접속(로컬/LAN)에서는 301→CORS 로 실패하고 영문 UI 가 나온다(2026-09-24 fixture 실측). | 기존 결함 기록 |

## 이관 원문

<!-- legacy CLAUDE.md 516-535 -->
## 웹 대시보드 카드 → 상세 URL

| 카드 | URL |
|------|-----|
| 총 신고 | `/data/all` |
| 보완 요청 | `/data/all?status=보완요청` |
| 처리중 | `/data/all?status=처리중` |
| 답변완료 | `/data/all?status=완료` |
| 취하 | `/data/all?status=취하` |
| 수용 | `/data/all?status=수용` |
| 일부수용 | `/data/all?status=일부수용` |
| 불수용 | `/data/all?status=불수용` |
| 주정차위반 | `/data/parking` |
| 과태료 | `/data/traffic?fine=과태료` |
| 경고/범칙금 | `/data/traffic?fine=경고` |
| 교통 불수용 | `/data/traffic?status=불수용` |
| 미확인 | `/data/traffic?fine=미확인` |

---


<!-- legacy CLAUDE.md 595-615 -->
### 웹 사이드바 설정 섹션
- **앱 설정 (/settings)**: 1. 시스템 및 서버 설정 / 2. 크롤링 설정 / 3. 외부 연동 키 설정
  - 1. 시스템: 로그인 계정 → 별점 휴대폰 번호 → 데이터 필터 → 세션/프록시
  - 2. 크롤링: 크롬 구동 방식 → 크롤링 후 자동 저장 → 고급 설정 → 자동 스케줄러
  - 3. 외부 연동: 텔레그램 → 구글 시트 URL → 구글 JSON 인증 파일 업로드 (fetch 방식)
- **관리자 계정 변경 (/settings/admin)**: 아이디/비밀번호 변경 전용 페이지
- **기기 연동 (/devices)**: API 키 생성/삭제 + 현재 WebSocket 연결 기기 목록
- **데이터 수정 (/db-editor)**: mysafetymerge 테이블 조회·수정 → mysafety + mysafetydetail 역동기화 (교통/주정차/기타 탭)

### 세션 인증 미들웨어
- `_PUBLIC_PATHS`: `/login`, `/setup`, `/logout`, `/health` 세션 우회
- `_PUBLIC_PREFIXES`: `/static/`, `/api/v1/`, `/ws/` 등 세션 우회
- `/api/v1/` → `X-API-Key` 별도 인증
- AJAX/fetch 요청은 302 대신 401 JSON 반환
- `/health` → 인증 없이 `{"status": "ok"}` 반환 (cloudflared health check 전용)

### 리버스 프록시 지원 (trusted_proxies)
`config.ini` `[SETTINGS] trusted_proxies` 에 쉼표 구분 IP 목록 저장.
`main.py` 모듈 레벨에서 `uvicorn.middleware.proxy_headers.ProxyHeadersMiddleware` 조건부 적용.
설정 변경 후 **서버 재시작 필요** (미들웨어는 앱 시작 시 1회 등록).


<!-- legacy CLAUDE.md 638-664 -->
### 웹 통계 탭 구조 (stats.html)
현재 `/stats`는 먼저 조건/조작 셸을 보내고 `/stats/content`와 `/stats/map/points`를 독립 로딩한다.
지도는 최대1200점, 화면 bounds/zoom으로 갱신하며 순번/abort로 과거 응답을 버린다. 원문 좌표·전체 모집단은 그대로다.
처리결과(일부수용 포함)와 처분은 별도 축, 제목 대신 저장 법규 조합을 집계한다(statistics-spec 현행 정정).

### 크롤링 로그 레이아웃
`crawlLayout`의 옵션/로그는 md부터 두 열, 좁으면 세로다. 범위 ops-section/form 경계를 정확히 닫고
로그에 260–620px 제한/내부 스크롤·긴 줄 줄바꿈을 둔다. 표시만 2000줄/256KiB로 제한·배치 갱신하며 원본 로그 파일은 유지한다.
WS 최초 이력은 마지막64KiB만 보내고 재연결 시 UI를 교체한다. 사용자가 과거 로그를 보면 자동 추적하지 않으며
"최신 로그 따라가기"와 파일 브라우저의 이전 로그 확인 경로가 있다. 페이지 이탈은 WS/재연결/flush 타이머를 정리한다.

### 지도 말풍선 장식
`report-map-tooltip`의 사용자 정의 connector 가상 요소가 Leaflet 기본 삼각형 border와 겹친다.
이 tooltip의 ::before/::after만 제거한다. 정상 div marker/cluster/선택/popup-tip/제공자 저작권은 유지한다.

### 웹 통계 탭 구조 (이관 설명)
2행 버튼 UI. 1행: 교통위반 / 주정차위반 / 기타위반. 2행: 기관별 / 담당자별 / 경찰 기관 / 경찰 담당자 / 비경찰 기관 / 비경찰 담당자.
Bootstrap tab 제거 → 커스텀 show/hide (`stats-pane` 클래스). 선택 상태 sessionStorage에 저장.
`get_agency_stats()` 반환값: `{"traffic": {...}, "parking": {...}, "other": {...}}`.
각 카테고리별 탭 클릭 시 해당 `/data/{category}?agency=...&agencyExact=true` 로 이동.

### 웹 통계 agencyExact 파라미터 (stats.py, data.py)
`agencyExact=True` 시 `df['처리기관'] == agency` 정확히 일치 필터 적용.
통계 행 클릭 링크에는 `&agencyExact=true` 자동 포함. 직접 검색 시는 기본값 `false` (contains).

### 웹 상세검색 문법 (data_table.html)
- 상세검색 상단에 `&` = AND, `,` = OR 안내 문구 표시.
- `차량번호`, `신고번호`, `ID`, `신고명`, `위반법규`, `담당자`, `위반장소`, `처리기관`, `범칙금_과태료`, `별점사유`, `신고내용`, `처리내용`은 DataTables `ext.search` 커스텀 필터에서 같은 문법으로 처리.
- 화면 순서: 차량번호·신고번호·ID를 먼저, 이후 PC `main` 통계표와 공통인 처리기관·담당자·과태료/범칙금·처리상태·별점을 둔다. 나머지는 신고명·위반법규·위반장소·신고내용·처리내용·보완횟수·만족도 조사 여부·별점사유·경찰기관 조건·날짜 순서다. 모바일 신고 목록 상세 검색도 같은 순서다. 통계 상세 검색은 없는 항목을 건너뛴다.
- 다중선택 옵션을 클릭하면 메뉴를 다시 그리므로 토글에 포커스를 돌린다. 이어서 Enter를 누르면 현재 선택 조건으로 검색한다.
- `처리상태`는 `_tableData`(DB에서 내려온 현재 레코드)에서 distinct 값을 추출해 다중선택 드롭다운으로 렌더링.
- `별점`은 `없음`, `1~5점` 다중선택 드롭다운으로 렌더링.
- 두 드롭다운 모두 선택된 항목 우측에 초록 `v`를 표시.
- `만족도 조사 여부`는 `참여 완료`, `참여 가능` 단일선택 `<select>` 드롭다운으로 렌더링. 모바일 `SearchFilterSheet`에도 동일하게 추가.

### 웹 통계 상세검색 문법 (stats.html, services/report_stats_service.py)
- `처리기관`, `신고명`, `위반장소`는 `_parse_and_or_groups()` / `_matches_and_or_text()` / `_apply_text_query()` 헬퍼로 같은 `&` / `,` 문법 처리.
- `agencyExact`는 단일어 입력일 때만 exact match를 사용하고, `&` 또는 `,`가 포함되면 AND/OR 부분검색 규칙이 우선한다.

### 웹 첨부파일 인라인 미디어 (data_table.html)
`renderAttach()` 버튼에 `data-type="photo"|"file"` 추가.
클릭 시 Bootstrap 모달(`#attachModal`)에서 이미지/동영상 인라인 표시, 기타는 다운로드 버튼.
`<img>` → 인라인 표시, `<video controls>` → 인라인 재생, 기타 → 다운로드 버튼만.

## 2026-09-29 기관 표시 설정

설정 화면의 경찰기관명 정규화 토글은 제거했다. 기관명 표시와 통계 묶음은 기관코드 registry의 현행명·통계 키를 항상 사용하며, 확인되지 않은 코드는 원문 이름을 유지한다.

웹 신고 목록은 조회 결과에서 registry 표시명을 계산한 다음 기관 검색을 적용한다. `/api/v1`의 신고 원문 `처리기관`·`처리기관코드`는 DB 교환 계약을 위해 그대로 내려가며, 모바일 Client가 수신 뒤 화면 표시명을 계산한다.
