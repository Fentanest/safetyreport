# 신고 분류 개편(5분류·SSNPC)과 저장 구조 통합 — 2026-10-11

통합 책임 Opus. 사용자 검토용 화면 계획: Artifact "신고 분류 개편 계획"(https://claude.ai/artifact/GxLduYCrbtjpTN3rqSSbWo).
자매 문서: 모바일 레포 `docs/plans/2026-10-11-report-taxonomy.md`(이 문서를 가리킨다).

## 결정 (사용자, 2026-10-11)
| ID | 결정 |
|---|---|
| T-1 | 분류는 안전신문고 상세 응답의 `SSNPC_CD`+`SSNPC_TYPE` 으로 정한다. 코드가 없을 때만 본문 접수 메뉴(`entry_value`). 정본 `contracts/report-categories.json`(통계 API 43개 코드). |
| T-2 | 5분류: 교통위반(TV)·주정차위반(IP·EP)·안전신고(CM)·생활불편(DR)·기타(통계에 없는 경로·코드 없음). (구) 코드 CM-06·CM-08 → 교통위반, CM-05 → 생활불편(권장안 적용 — 사용자 이의 시 바꿈). |
| T-3 | 서버 DB 구조를 신고 한 건 한 행으로 통합(스키마 6), 모바일 17. 버전 5/16 은 제자리 변환, 그 미만은 기존 백업 뒤 비우기. |
| T-4 | 백업 복원은 같은 버전끼리만(복원 뒤 초기화 크롤링을 하므로). |
| T-5 | 업데이트 후 초기화 크롤링 1회 더(`source-rebuild-2026-10-11.1`). 안내에 "서버 사양 개선에 따른 추가적인 불가피한 조치입니다. 불편을 드려 죄송합니다." Standalone 은 모바일이, Client 는 서버가, PC 는 PC 가. 바뀐 분류는 공유에 다시 올라간다(capture 가 바뀐 payload 만 보냄). |
| T-6 | 차량번호: `SPLMNT_VHRNO`(보완 완료) → `VHRNO` → 본문 정규식. |
| T-7 | 화물차 밤샘주차 판별은 EP-02 기타불법주정차(또는 소분류 없음)일 때만. 소분류별 최저 추정 과태료(소방차전용구역 50만, 소방기본법 시행령 별표3 제2호 아목 1차). |
| T-8 | Client 앱이 켤 때마다 초기화 화면을 거치는 문제 수정. |
| T-9 | GitHub Variables: safetyreport `LOCAL_SUPABASE_URL`·`LOCAL_SUPABASE_PUBLISHABLE_KEY`, map `LOCAL_PUBLIC_ANALYTICS_URL`·`LOCAL_SUPABASE_PUBLISHABLE_KEY`·`LOCAL_SUPABASE_URL`, mobile 기존 `LOCAL_*`, auth 그대로. 워크플로 이름만(값은 사용자). |
| T-10 | 클라우드 Supabase 업로드는 저장 구조 개편(1단계)이 끝나면 막는다(`COMMUNITY_INGEST_ENABLED=false`). 로컬(셀프호스트) Supabase 테스트 데이터 초기화 → 5분류 마이그레이션 → 클라우드 데이터 이전. |
| T-11 | 작업 배분: 쉬운 것 Sol(codex gpt-6.1-sol), 중간 Sonnet, 어려운 것 Opus. Astra(codex gpt-6-astra) 검수 → 별도 Opus 최종 감사. 기능 브랜치 커밋까지. |

## 저장 구조 (서버 6 / 모바일 17)
- `mysafety_report`: 목록 + 상세 + `category`·`entry_value`·`ssnpc_cd`·`ssnpc_type`. category NULL = 목록만 본 신고(상세 미저장).
- `mysafety_report_effective`: 화면용 = report(category 있는 행) + 수정값 + 감시목록 + 6개월 첨부 가림. 바뀐 ID 만 갱신.
- `mysafety_report_body`: 본문 원문.
- 없어진 표: `mysafety`, `mysafetydetail_*`, `mysafetymerge_*`, `mysafety_entry_value`, `mysafety_raw_content`.
- 모바일 `reports` 는 같은 열(+ legacy `raw_content`). 서버 DB 가져오기는 category 있는 행만.
- 모바일 API: `X-SR-Taxonomy: 5`(또는 `?taxonomy=5`)를 보내지 않는 앱에는 `/api/v1/*` JSON 의 category 값 safety·living 을 other 로 접는다(`core/utils/taxonomy_compat.py`). `/reports/other` 는 구앱에 safety+living+other. `/server/version` 의 `report_taxonomy: 5`.

## 4단계 — 기타 탭 세분류 영향 범위 (2026-10-11 Opus 조사)
목표: "기타" 한 칸을 **안전신고·생활불편·기타** 세 칸으로. 통계·지도·편집기·내보내기·목록 모두 5분류. 라벨: 교통위반·주정차위반·안전신고·생활불편·기타
(`report_category.CATEGORY_LABELS`). "기타위반" 라벨은 "기타" 로 바꾼다. 색: 안전신고·생활불편 토큰을 새로 정한다(라이트·다크 대비 검사).

### PC(서버)
| 영역 | 파일 | 할 일 |
|---|---|---|
| 목록 경로 | `web/routers/data.py`, `web/templates/base.html` 사이드바 | `/data/safety`(안전신고 조회)·`/data/living`(생활불편 조회) 추가, `/data/other` 는 기타만 |
| 통계 집계 | `services/stats/reads.py`(`_load_stats_frames` 3프레임 → 5), `services/stats/metrics.py`·`overview`·`dashboard.py`, `web/routers/stats.py`(records_* 키) | 5분류 집계. 웹 화면은 항상 5분류. 모바일 API(`/api/v1/stats*`·`/summary`)는 `X-SR-Taxonomy: 5` 를 보낸 앱에만 safety·living 키를 더하고 other 를 기타만으로, 구앱에는 지금처럼 3분류(other = 안전신고+생활불편+기타) |
| 통계 화면 | `web/templates/stats.html`, `web/static/ui/stats.js`(CAT_LABELS), `stats-loader.js`, `stats.css` | 분류 버튼·상세표 5개 |
| 지도 | `services/stats/map.py`(category_breakdown), `web/routers/filters.py`, `web/templates/report_map.html`, `report-map.js`, `report-map-calc.js` | 분류 필터 5개, 묶음 원 분류 집계 5개 |
| 데이터 수정 | `web/templates/db_editor.html` | 분류 5개 |
| 상세 | `web/static/ui/report-detail.js` | 분류 → 목록 경로 |
| 대시보드 | `web/templates/index.html`, `services/stats/dashboard.py` | 분류별 건수 5개(있다면) |
| 내보내기 | `core/database/report_reads.load_results_by_category` | 이미 5시트 |
| 계약 | `contracts/stats-overview-vectors.json`, `report-filter-vectors.json` 등 | 5분류 벡터(두 레포 바이트 동일) |

### 모바일
| 영역 | 파일 | 할 일 |
|---|---|---|
| 신고내역 탭 | `lib/screens/report_list_screen.dart`(교통위반·주정차·기타위반·중복차량) | 교통위반·주정차·안전신고·생활불편·기타·중복차량. PROJECT_RULES "4개 분류" 문구 갱신(사용자 지시로 변경) |
| 상태 | `lib/providers/report_provider.dart`(`_kCoreCategories`, fetch, 라벨) | 5분류 |
| Client API | `lib/services/api_service.dart` | 요청에 `X-SR-Taxonomy: 5`, 5분류 허용. 서버가 `report_taxonomy` 를 모르면(구서버) 3분류로 동작 |
| 목록 | `filtered_list_screen.dart`, `widgets/local_paged_report_list.dart` | 5분류 |
| 통계 | `statistics_screen.dart`, `services/local_statistics.dart`, `models/stats_overview.dart`, `models/agency_stats.dart` | 5분류(서버 응답 새 키, 없으면 3분류) |
| 지도 | `report_map_screen.dart`, `local_db_service` 지도 분류 | 5분류 |
| 데이터 수정 | `data_editor_screen.dart` | 5분류 |
| 내보내기 | `services/excel_export_service.dart` | 시트 5개 |
| 테마 | `theme/sr_colors.dart` `category()` | safety·living 색 |
| 별점 | `rating_management_panel.dart` | 라벨 |
| 파서 | `services/standalone_parser.dart` `categoryOf` | `report_category.categoryOf(ssnpc, entry)` |

### 6단계 — 공유 계약 5분류 (세 레포)
`contracts/community-ingest/observation.md`·`observation.schema.json`·`vectors/observations.json`·`rebuild.md`(안내 문구·버전), MANIFEST 재계산,
서버 `services/community_capture.build_payload`, 모바일 `lib/community/capture/observation_rules.dart`, 지도 레포 Supabase 마이그레이션(제약·집계 함수 `'all','traffic','parking','other'`)과 필터 UI.

## 진행 기록
| 단계 | 커밋 | 결과 |
|---|---|---|
| 0 계약 | safetyreport `80f1c30`, mobile `7d37948d` | 43개 코드, 운영 사본 3,082건 모두 코드로 풀림 |
| 1 저장(서버) | `7202292` | 운영 사본(5월→10월 스냅숏, 3,082건) 제자리 변환: 행·값 해시 일치, 25건 분류 이동(생활불편 24·안전신고 1) |
| 1 저장(모바일) | `60b9f40d` | v17, 서버 6 가져오기 |
| 3 초기화(서버) | `e07a8d1` | 버전 `source-rebuild-2026-10-11.1`, 안내 문구 |
| Sol | `e327e7b`·`84e67a2`·`da465b12`·`b207e324`·`5fbf270` | 과태료 코드 기반, Variables, Dart 분류기 |
