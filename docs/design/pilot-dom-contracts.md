# 시범 범위 DOM/JS 계약 (base · data_table · stats · index)

작성 2026-09-24, 기준 `17df6cb`. 리뉴얼(마크업·클래스·색 변경) 때 깨지면 기능이 죽는 결합만 모았다.
전체 기능 목록은 [feature-matrix.csv](feature-matrix.csv), 기계 인벤토리는 `python3 scripts/dev/web_contract_inventory.py`.
줄 번호는 독립 조사(Claude Explore) 결과를 Opus가 표본 대조했다. 바꾸기 전 인벤토리를 다시 뽑아 비교한다.

## 1. 유지해야 할 선택자

| 템플릿 | 선택자 | 누가 쓰나 |
|---|---|---|
| base.html | `#btnSidebarToggle`, `#sidebarOverlay`, `#mainSidebar`, `.sidebar-open` | openSidebar/closeSidebar (212-230) |
| base.html | `#sidebarVersionStatus` | checkVersion (162-183) |
| base.html | `#reportDetailModal`, `#rdName`, `#rdStatusBadge`, `#rdFields`, `#rdReportContent(Text)`, `#rdProcessContent(Text)`, `#rdMedia(List)`, `#rdVideos`/`#rdVideoList`, `#rdFiles`/`#rdFileList`, `#rdSupplements(Body/Badge)`, `#rdViewBtn` | window.showReportDetail (620-751), renderSupplementHistory, showDetailRenderFallback |
| base.html | `a.report-detail-link[data-report]`(document 위임) | index.html:342,397 / watchlist.html:50 / duplicate_groups.html:226 |
| base.html | `video[data-source-url][data-proxy-src]`, `[data-proxy-video-scope]`, `[data-proxy-video-status]` | prepareProxyVideos / resetProxyVideos |
| base.html | `.floating-search-btn`(z-index 1050) + offcanvas show/hide 리스너 | data_table.html:94, stats.html:78 |
| data_table.html | `.btn-add-watchlist`, `.btn-enqueue-crawl`, `.btn-export-excel`, `.btn-copy-selected-ids`, `.btn-copy-page-ids`, `.btn-copy-ids` (상·하단 툴바가 같은 클래스 공유) | 1085-1334 |
| data_table.html | `.row-checkbox`, `#selectAll`, `tr.row-selected` | 선택 Set 유지 (667, 698-713, 1261-1280) |
| data_table.html | `#search*` 입력 20여 개, `#excludePolice/#onlyPolice.filter-police`, `#searchStatusDropdown`/`#searchRatingDropdown` 내부 `.multi-select-toggle/.multi-select-menu/.selection-text/.multi-select-option` | ext.search (772-839), renderMultiSelect |
| data_table.html | `#topScrollWrapper`, `#topScrollDummy` + DataTables `.dataTables_scrollBody/.dataTables_scrollHead` | syncScroll (727-747) |
| data_table.html | `.view-all-btn[data-links][data-type]`, `#attachModal(Label/Body)`, `#textModal(Title/Content)` | 937-986, 1200-1259 |
| stats.html | `#statsYearGroup .stats-year-btn[data-year]`, `#statsCatGroup .stats-cat-btn[data-cat]`, `#statsTypeGroup .stats-type-btn[data-type]` | 739-810 |
| stats.html | `.stats-pane#<cat>-<type>`(18개), 표 id `statsTable<Cat><Type>` | showPane, DataTable init |
| stats.html | `#statsColumnCheckboxes`, `#statsColumnScope`, `#statsColumnsSelectAll`, `.stats-column-checkbox[data-column-key]` | 572-657, 778-794 |
| stats.html | `#statsLawSidebar` (2026-09-24 오른쪽 세로 패널 → 표 위 가로 줄로 이동, id 유지) | renderLawButtons |
| index.html | `#sunwi*` (Prev/Next Parent/Child Category, Items, Content, UpdatedAtLabel), `.progress-bar[data-width]` | initSunwiWidget, animateProgressBars |

## 2. 텍스트가 계약인 곳
- data_table 헤더 `th` 텍스트 = CSV 열 이름(1024, 1046) = 더블클릭 모달 제목(1255). 헤더 문구를 바꾸면 내보낸 파일 열 이름이 바뀐다.
- stats 헤더 텍스트 = sessionStorage `stats_column_visibility` 키(`getColumnLabel`, `★`→`별점`, `비율`→`<앞 열> 비율`). 바꾸면 저장된 열 설정이 끊긴다.
  2026-09-24 표 폭 조정으로 `비율` 열을 건수 칸에 합쳤다(13열). 예전 `… 비율` 키는 남아 있어도 무시된다. 합계 행 칸 수 = 머리글 칸 수(`tools/web-tests/specs/stats-layout.spec.ts`).
- sessionStorage 키: `stats_cat`, `stats_type`, `stats_column_visibility`. localStorage 사용 없음(테마 저장 키를 새로 만들 때 충돌 없음).

## 3. JS 가 클래스 이름으로 색을 바꾸는 곳 (CSS 만 바꾸면 되돌아감)
- stats.html:747-761 `catColorMap` — `btn-primary|warning|success` ↔ `btn-outline-*`, 구분 탭 `btn-secondary` ↔ `btn-outline-secondary`.
- stats.html:700-704 법규 버튼 `btn-secondary|btn-warning|btn-info|btn-outline-secondary`.
- stats.html:172,176 연도 `btn-dark`/`btn-outline-dark`(Jinja).
- base.html:170-178 버전 상태 인라인 색, 436-441 상태 배지, 572-589 보완 이력 `bg-white/bg-light`, 702-733 미디어 래퍼 `bg-light` + `background:#000`.
- data_table.html:504-513 배지(fallback `bg-light text-dark border`), 580 보완 배지, 949-963 첨부 항목.

## 4. 전역 선택자 부작용 (새 요소를 넣을 때 주의)
- stats.html:838 `$('form').on('submit')` — 페이지의 **모든 form** 에서 빈 input 을 disabled 로 만든다(테마 스위치를 form 으로 만들면 영향).
- index.html:439 모든 `.progress-bar` 애니메이션, data_table.html:886 모든 `input[type=date]`, data_table.html:33 전역 `th {}` 스타일(모달 안 표 포함), base.html:228-230 사이드바 모든 `a` 클릭 시 사이드바 닫힘.

## 5. 층위(z-index)
`.floating-search-btn` 1050 > Bootstrap offcanvas 1045 — base.html:234-241 리스너가 숨겨서 동작한다. 사이드바 1044-1046.
새 헤더/토스트/테마 메뉴는 이 값과 modal(1055)·offcanvas 사이에서 정한다.

## 6. 다크 테마에서 깨질 하드코딩 (주요)
| 템플릿 | 위치 |
|---|---|
| base.html | body `#f8f9fa`(23), 사이드바 `#343a40/#495057/#0d6efd`(24-26), `.bg-light` 본문 박스(776,780), 보완 배지 `#fd7e14`(783) |
| data_table.html | `<style>` 18,40(`tr.row-selected td #fff3cd !important`),49,51(`.multi-select-toggle #fff`),79,82 ; `alert-light`(107) |
| stats.html | `.stats-column-controls` 그라데이션(10), `.stats-column-item #fff`(25), `card-header bg-white`(60), `text-bg-light`(223), `alert-light`(93) |
| index.html | `bg-white` 3곳, `bg-light` 5곳, `text-dark` 11곳, `table-light` 2곳, 인라인 색 9곳, Sunwi 카드 흰 그라데이션(60)·`#212529` 글자(42,93) |
| 공통 | flatpickr 는 라이트 CSS 만 로드(base.html:20), DataTables 기본 스타일 |
`#fd7e14`(보완요청 주황)는 8곳에 하드코딩 — 토큰 하나(`--sr-status-supplement`)로 모은다.

## 7. 알려진 기존 결함 (리뉴얼과 무관, 기록만)
- `bi bi-info-circle` 아이콘(stats.html:65)은 Bootstrap Icons 를 로드하지 않아 빈칸.
- KeyTable/AutoFill/Select 확장은 base 에서 전역 로드되지만 이 4개 템플릿에서는 켜지 않는다(다른 템플릿 확인 전 제거 금지).
- DataTables `language.url` 이 프로토콜 상대 경로라 http 접속에서 한국어 파일이 CORS 로 실패(baseline BL-1).
- stats 드릴다운 URL 비인코딩·`__없음__` 전달(statistics-spec D-STAT-8), footer 합계 고정 인덱스(D-STAT-7).
