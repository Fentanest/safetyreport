# 웹UI 첫 시범 구현 계획

작성 2026-09-24. 상태: **P0 사용자 승인(2026-09-24)**. P1 착수(Gemini G10, 브랜치 `gemini/p1-list`), P2 는 P1 검수 뒤. 로고는 LOGO 시안 채택(`f442759`).
근거 문서: [../design/ui-renewal-spec.md](../design/ui-renewal-spec.md), [../design/pilot-dom-contracts.md](../design/pilot-dom-contracts.md),
[../design/statistics-spec.md](../design/statistics-spec.md), [../testing/web-ui-test-plan.md](../testing/web-ui-test-plan.md).

## 1. 범위
| 단계 | 화면 | 포함 | 제외 |
|---|---|---|---|
| P0 공통 셸·테마 | `base.html`, `login.html`, `setup.html` | 토큰·light/dark/system 전환(첫 paint 적용), 사이드바·헤더·카드·버튼·배지·모달·offcanvas·flatpickr·DataTables 기본 색, base.html JS 문자열 안 하드코딩 색 정리, 보완요청 주황 토큰화 | 새 메뉴·전역 검색·알림 벨 |
| P1 대표 목록 + 상세 모달 | `data_table.html`(5개 목록 공용), `base.html` 상세 모달 | 작업 바, 표(선택 행·정렬·가로 스크롤·배지), 상세검색 offcanvas·다중선택, 첨부·텍스트 모달, 상세 모달 레이아웃 | 인라인 필터 바(NO-10)는 승인 시에만, 필터 의미 변경 없음 |
| P2 통계 상단 | `stats.html` 상단부 | 제목·필터 요약, 연도/카테고리/구분 탭 재스타일(`catColorMap` 등 JS 매핑 동시 수정), 배너(결정 D-STAT-1 반영), 열 선택 패널 | KPI·월별 차트는 아래 선행 조건 충족 후(P2b) |

나머지 화면(대시보드 포함)은 시범 승인 후 같은 토큰으로 확산한다. 대시보드는 하드코딩이 가장 많아(index.html) P3 첫 번째로 둔다.

## 2. 사용자 결정이 필요한 것 (시범 착수 전)
1. **테마 기본값**: 제안 = `system`(OS 따라감) + 사이드바 하단 3단 토글(시스템/라이트/다크).
2. **로고**: 제안 = 현행 카메라 로고 유지(PC 시안과 같은 계열). 방패 로고로 바꿀지.
3. **BL-1 수정 포함 여부**: DataTables 한국어 파일 URL 을 `https://` 로 고정(한 줄×3곳, http 접속에서 영문 UI 해결). 제안 = P0 에 별도 커밋으로 포함.
4. **통계 정의**(P2b 선행): D-STAT-3 처리일(달력 날짜 차이로 통일 권장, 기존 표 숫자 변경), D-STAT-1 배너(필터 반영 권장), D-STAT-2 "미확인" 라벨.
5. **모바일 브랜치 순서**: `feature/stats-overview-api`(모바일 세션, 미커밋)를 먼저 `dev` 에 합친 뒤 P2b 를 같은 집계 함수로 구현하는 순서 제안.
6. 웹폰트 도입 여부(기본은 시스템 서체).

## 3. 파일 소유권 (동시 덮어쓰기 방지)
| 파일 | 소유자 | 비고 |
|---|---|---|
| `web/static/ui/tokens.css`, `theme.css`, `theme.js`(신규) | Opus | 토큰 이름은 P0 커밋에서 고정, 이후 변경은 Opus 경유 |
| `web/templates/base.html` | Opus | P1 의 상세 모달 부분도 Opus 가 편집(한 파일 한 소유자) |
| `web/templates/login.html`, `setup.html` | Opus | |
| `web/templates/data_table.html` | Gemini(P1 implementation) | 토큰만 사용, base.html 수정 필요 시 요청서로 |
| `tools/web-tests/specs/list-*.spec.ts` | Gemini | |
| `web/templates/stats.html`, `tools/web-tests/specs/stats-*.spec.ts` | Opus(P2) | |
| `tools/web-tests/specs/theme-*.spec.ts`, `playwright.config.ts` | Opus | |
| `services/report_stats_service.py` | P2b 전까지 수정 금지 | 모바일 브랜치와 충돌 방지 |

## 4. 진행 순서와 검수
1. **P0 (Opus)** — dev 에서 구현 → 인벤토리 diff(사라진 id 0), unittest, Playwright(chromium·firefox, 1440·390, 두 테마) + `theme` spec(전환·새로고침 유지·`data-bs-theme` 선적용) + axe(신규 위반 0).
   → **Gemini G3(review)**: 별도 worktree·포트·세션으로 두 테마 동선 재현, 대비·포커스·z-index 점검, 근거 줄/재현 명령 필수.
2. **P1 (Gemini G4, implementation)** — P0 커밋 기준 worktree. 목록 동선 spec(상세검색 AND/OR, 다중선택, 페이지 이동 후 선택 유지, 번호 복사 3종 범위, CSV 헤더·행 수, 감시 추가, 크롤링 차단 메시지, 첨부·텍스트 모달) + 두 테마 캡처.
   → **Opus 검수**(diff·재현·캡처) 후 dev 에 통합.
3. **P2 (Opus)** — 통계 상단. `stats` spec(표 값 = unittest 기대값, 탭·연도·법규·열 선택 유지, 드릴다운 URL 인코딩 재현 테스트 먼저).
4. **사용자 시범 검토**: fixture 서버 두 테마 실제 캡처 세트(1440·390) 제출 → 승인된 렌더만 golden 으로 등록.
5. 승인 후 P2b(통계 KPI·월별) → P3 이후 화면 확산.

검수 트리거: 각 단계 경계(영역 전환), 누적 약 5파일/800줄. 작은 CSS 수정마다 재호출하지 않는다.

## 5. 완료 기준 (시범)
- feature-matrix 의 SH-*, DL-*, ST-* 행 동작 보존(자동 테스트 또는 기록된 수동 확인).
- 두 테마 × Chromium·Firefox × 1440·390 캡처, 콘솔 신규 오류 0(BL-1 은 수정 시 0), axe 신규 위반 0.
- source fixture + 개발 Docker 에서 새 CSS/JS 200 응답. PyInstaller 번들은 not-run 으로 기록(해당 runner 없음).
- `main` push·VERSION·태그·릴리즈 없음.

## 6. 규모 예상
P0 신규 CSS/JS 약 400~600줄 + base/login/setup 수정 150줄 내외, P1 data_table 스타일·마크업 200~300줄 + spec 200줄, P2 stats 상단 150줄 + spec 100줄.
