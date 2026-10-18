# 2026-10-18 통계 전체 분류 탭 검수 (구현 Astra, 검수 Opus)

명세: [docs/plans/2026-10-18-stats-all-tab.md](../plans/2026-10-18-stats-all-tab.md). 서버·모바일 `stats-all-tab` 브랜치.
계기: 205 사용자 DB 의 장애인전용구역 법규가 통계 법규 목록에 없다는 문의 → 목록이 분류 탭별이고 기본 탭이 교통위반이라서였다.
사용자 결정: 전체 탭 추가·기본 탭, 구현 Astra(codex `gpt-6-astra`), 검수 Opus.

## 검수 결과

| 항목 | 결과 | 근거(`.agent-runs/stats-all-1018/`) |
|---|---|---|
| 서버 unittest 전체 | passed — 1,303 OK, skip 5 | `opus-server2.log` |
| 모바일 `flutter test` 전체(3.47.5) | passed — 2,324, skip 18 | `opus-flutter-test2.log` |
| 모바일 `flutter analyze` | passed — No issues | 수정 뒤 재실행 |
| 서버↔모바일 계산 동등성(`scripts/dev/logic_parity_check.py`) | passed — 45 조합, 차이 0(한쪽 전용 키는 기존 종류와 같음) | `opus-parity.log` |
| 웹 브라우저(Chromium·Firefox × 라이트 1440 / 다크 390) | passed — 전체 기본 선택, all 총계 = 5분류 합(21), 법규 목록 = 합집합, `/data/all` 행 링크, 분류 전환·복귀, 콘솔 오류 0, 가로 넘침 0 | `stats-all-*.png` |
| Playwright 저장 spec(`stats-top`, `category-split`) | not-run — 로컬 통합 스택의 동의 정책이 없어 fixture 세션 주입 실패 | 대신 위 브라우저 확인 |
| 실기기·Docker·PyInstaller | not-run | |

브라우저 확인은 fixture 데이터로 띄운 서버에서 커뮤니티 게이트 상태만 'ok' 로 고정한 시험 실행기(저장소 밖)로 했다. 관리자 로그인·세션·CSRF 는 제품 그대로다.

첫 검증에서 실패 2건:
- 서버 `test_community_upload_control...share_one_run...` 1건: Flutter 와 동시 실행한 부하 때 실패, 단독 3회 통과·순차 전체 재실행 통과 → 시간 의존 시험의 부하 흔들림(이번 변경과 무관).
- 모바일 `report_list_drilldown_test` "통계 기관 카드 → 목록": **실제 결함**. 아래 Opus 수정 2.

## Opus 수정

1. **전체 법규표 소속 범위**: 초안은 전체 `by_law` 에서도 상위 조문 소속을 분류별 범위로 묶었다(분류별 합 불변식을 지키려고 — 명세가 법규표까지 합 불변식을 요구한 것이 과했다).
   그러면 전체 탭 안에서 법규 필터 결과·요약 법규 막대(`overview.all`)·법규표가 어긋난다(공통 벡터: 안전신고에만 `제32조제1항` → 필터 `제32조` 4건, 표 3건).
   PROJECT_RULES 3(같은 화면은 같은 범위)을 우선해 서버 `services/stats/metrics.py` 변경을 되돌리고 모바일 `local_statistics.dart` 도 한 범위로 바꿨다.
   시험은 법규표를 합 불변식에서 빼고, 분류를 넘는 상위 조문 건수(제32조 4·제32조제1항 2)와 "법규표 L 건수 = 법규 필터 L 결과의 L 건수"를 단언한다.
2. **모바일 추이 차트 RangeError**: 전체(막대 82개)에서 교통(81개)으로 바꾼 직후 차트를 끌면 fl_chart 가 길이가 다른 막대 목록 사이 애니메이션 중 옛 인덱스로 `RangeError` 를 던졌다.
   예전에도 분류 전환 직후에 잠재했으나 기본 탭이 전체가 되며 흔한 경로가 됐다. `BarChart` 에 막대 구성 키를 줘서 구성이 바뀌면 새 차트를 만든다.
3. 문서(서버 statistics-spec·data-contracts, 모바일 data-contracts·CHANGELOG)를 위 규칙으로 정정.

## 코드 검토 메모
- 서버 `all` 은 projection·필터 뒤 원자료 프레임을 합쳐 기존 `calc_stats` 로 재집계 — 처분·추정액은 행의 실제 category/entry_value 로 판정, 확정·추정 금액 분리 유지.
- `/api/v1/stats` 는 라우터 변경 없이 `data.all` 이 추가된다(taxonomy 3/5). 기존 키 보존은 시험으로 확인.
- 모바일: 구서버(응답에 `all` 없음)면 전체 칩을 숨기고 교통위반. 전체 카드의 목록 이동은 `FilteredListScreen(category: 'all')`.
