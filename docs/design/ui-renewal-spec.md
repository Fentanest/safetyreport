# 웹UI 리뉴얼 정본

작성 2026-09-24. 대상은 safetyreport **서버 웹UI**(FastAPI/Jinja2). 모바일 앱은 별도 저장소이며 하단 탭 구조를 데스크톱에 이식하지 않는다.

## 1. 정본 우선순위
1. 기능: 사용자 요구 + 실제 route/service/DB/DOM 계약 → [feature-matrix.csv](feature-matrix.csv), [pilot-dom-contracts.md](pilot-dom-contracts.md)
2. 시각: `reference/01-pc-light.png`, `reference/02-pc-dark.png` (둘 다 1672×941, sha256 은 [asset-manifest.csv](asset-manifest.csv))
3. 보조: 03~14 보드(토큰·컴포넌트·배지·상태·아이콘·로고·배너). PC 시안과 충돌하면 PC 시안이 이긴다.
4. frontend-design 스킬은 품질 보조(타이포 스케일·대비·모션 절제)로만 쓴다. "새롭고 과감한 컨셉" 제안은 이 작업 범위가 아니다.

시안의 숫자·문구·사진·버튼은 요구사항이 아니다. 시안 요소 분류는 기능표의 `NO-*`/`STAT-*` 행을 따른다:
전역 검색창·알림 벨·증감률·통계 엑셀 버튼·수집 현황 합산 패널은 제외 또는 결정 대기, 월별 추이·기관 TOP5·사진 캐러셀·인라인 필터 바는 기존 기능 재배치 범위의 제안.

## 2. 디자인 방향 (시안에서 읽은 것)
- 다크: 깊은 네이비 배경, 한 단계 밝은 네이비 패널, 얇은 파랑 경계, 파랑/시안 강조. 선택된 탭·주요 버튼만 채움 파랑.
- 화이트: 밝은 회청 배경, 흰 패널, 짙은 남색 텍스트, 같은 파랑 강조. 두 테마의 정보 배치는 같다.
- 상태 색 의미(두 테마 공통): 수용=초록, 일부수용=노랑/주황, 불수용·기타=빨강, 처리중=파랑/회색, 보완요청=주황(`#fd7e14` 계열), 취하=회색, 과태료=분홍/자홍.
  배지는 CSS + 실제 텍스트로 만든다(07-status-badges.png 를 잘라 쓰지 않는다).
- 사이드바: 로고 + 제품명 + 버전, 섹션 제목(데이터 조회/설정/기타), 아이콘+텍스트 메뉴. 넓은 화면 고정, 좁은 화면 offcanvas.
- 네온/글로우는 선택·강조에만. 표 본문·긴 텍스트는 평평하게. `prefers-reduced-motion` 존중.

## 3. 구현 방법 (결정)
| 항목 | 결정 | 근거 |
|---|---|---|
| 테마 전환 | `<html data-bs-theme="light|dark">` + 사용자 선택(light/dark/system) localStorage 키 `sr-theme` | Bootstrap 5.3 color modes; 현재 localStorage 사용처 없음(충돌 없음) |
| 첫 paint | `<head>` 안 짧은 인라인 스크립트로 저장값/시스템 선호 적용, localStorage 예외 처리 | 테마 깜빡임 방지 |
| 토큰 위치 | `web/static/ui/tokens.css`(색·간격·반경·그림자 custom properties), `web/static/ui/theme.css`(Bootstrap 변수 매핑·공통 컴포넌트), `web/static/ui/theme.js`(전환 UI) | web/static 은 PyInstaller add-data·Docker 에 자동 포함 |
| 캐시 무효화 | 기존 `?v={{ request.state.app_version }}` 패턴을 새 CSS/JS 에도 적용 | base.html:107 logo 선례 |
| 템플릿 분리 | 반복 요소(페이지 헤더, 카드, 상태 배지, 빈 상태)는 `web/templates/components/*.html` macro 로 점진 추출. DOM 계약은 유지 | pilot-dom-contracts.md |
| 차트 | 통계 차트가 필요할 때 Chart.js 를 jsdelivr CDN 으로(현재 모든 UI 라이브러리가 CDN). 오프라인 번들링은 별도 결정 | 현재 차트 라이브러리 없음 |
| 서체 | 1차는 시스템 한글 서체 스택 유지. 웹폰트(예: Pretendard)는 라이선스 확인·사용자 승인 후 | 폰트 라이선스는 사용자 확보 |
| 로고 | 1차는 현행 `web/static/logo.png`(카메라, PC 시안과 동일 계열) 유지. 방패 로고(13-logo-lockups)는 **결정 대기** — 합본·가장자리 번짐으로 그대로 사용 불가 | asset-manifest |

## 4. 화면별 방향
- 대시보드: 상태 카드 8 + 교통 처분 4 + 비율 바 2 + Sunwi 패널 + 감시목록/최근 답변 표. 시안처럼 카드 그리드로 재배치하되 링크 대상과 수치 정의는 현행 유지.
  월별 추이·기관 TOP5 는 `STAT-01/02` 정의 확정 후.
- 목록(data_table): 상단 작업 바(감시/크롤링/CSV/번호 복사 3종) → 표. 상세검색 offcanvas 유지(인라인 필터 바는 같은 필터의 바로가기로만). 선택 행·정렬·가로 스크롤·페이지 표시.
- 상세 모달: 제목+상태 배지, label-value 격자, 신고내용/처리내용 본문, 첨부(사진·동영상·파일), 보완 요약, "안전신문고 앱에서 보기".
- 통계: 필터 요약 → (정의 확정 후) KPI·월별·결과 비율 → 기존 연도/카테고리/구분 탭 → 열 선택 → 표 + 법규 사이드바. 차트는 표를 대체하지 않는다.
- 관리 화면(별점/감시/중복/크롤링/설정/기기/파일/백업/데이터 수정/지도): route·동작 그대로, 위험 동작(삭제·복원·크롤링 시작) 버튼을 시각적으로 분리. 시범 승인 뒤 확산.

## 5. 반응형·접근성
- 1280/1440/1920 폭에서 사이드바 + 가변 본문(본문 고정폭 제한 없음), 390 폭에서 offcanvas 사이드바·가로 스크롤 표.
- modal/offcanvas 는 뷰포트 안에서 스크롤, z-index 는 pilot-dom-contracts §5 기준.
- 테마 전환 시 필터·페이지·선택·열 설정·로그인 상태 유지(현재 상태는 sessionStorage·메모리이므로 새로고침 없는 전환이면 유지됨).
- 본문 대비 WCAG AA, 키보드 포커스 가시, 200% 확대에서 기능 손실 없음. baseline axe 위반 수는 docs/testing/baseline-2026-09-24.md.

## 6. 자산
reference 원본은 `docs/design/reference/`(Git 미추적, sha256 은 manifest). 실제 쓰는 자산만 정제해 `web/static/ui/` 에 둔다.
아이콘은 현재 FontAwesome 6.4(CDN)를 유지하고 12-icon-sheet 는 참고만. 배너(08/09)의 글자는 이미지에서 분리해야 하므로 앱 화면 대형 히어로로 쓰지 않는다.

## 7. 시범 검수
공통 셸·테마 + 목록/상세 모달 + 통계 상단을 fixture 서버에서 두 테마로 실제 실행해 캡처한다([../plans/web-ui-pilot-plan.md](../plans/web-ui-pilot-plan.md)).
사용자가 승인한 실제 렌더만 이후 비교 기준(golden)으로 삼는다. 생성형 시안 픽셀을 golden 으로 쓰지 않는다.
