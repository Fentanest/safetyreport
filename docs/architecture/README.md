# docs/architecture — 기술 문서 색인과 이관 대응표

2026-09-24 에 루트 `CLAUDE.md`(50,748바이트, 725줄)를 주제별 문서로 나눴다. 기준 커밋은 `17df6cb`.
원문은 [legacy-claude-reference.md](legacy-claude-reference.md)에 바이트 그대로 보관한다(자동 import 대상 아님).
각 주제 문서는 원문 절을 **문구 그대로** 옮기고, 상단의 "코드 대조 정정" 표에 원문과 현재 코드의 차이만 적는다.

| 파일 | 다루는 것 | 언제 읽나 |
|---|---|---|
| [overview.md](overview.md) | 디렉토리 구조, 전체 동작 요약(크롤링 갈래·중복군·미디어 프록시·지오코딩·Sunwi 등), 실행 모드/리소스 경로/DB 마이그레이션, 로그, 빌드 | 구조를 처음 파악할 때, 서버 기동·경로·빌드 작업 전 |
| [data-contracts.md](data-contracts.md) | 설정 키, DB 테이블/컬럼, 서비스 반환 키, category 전파, 모바일 API, WebSocket, 완료 마커 파일, 크롬 확장 | 데이터·API·집계를 건드리기 전 (모바일/크롬 확장 소비자 계약) |
| [crawling-and-processing.md](crawling-and-processing.md) | start.py 파이프라인, 파서 규칙, 카테고리 분류, 첨부 URL, 별점 API, extractor | 크롤러·파서 작업 전 |
| [web-ui.md](web-ui.md) | 대시보드 카드 URL, 사이드바/세션/프록시, 통계 탭·상세검색·agencyExact, 첨부 미디어 | 웹UI 작업 전 (리뉴얼 계약은 `docs/design/` 도 함께) |
| [legacy-claude-reference.md](legacy-claude-reference.md) | 원문 보관 | 이관 누락이 의심될 때 대조 |

## 절별 대응표 (원문 줄 → 새 위치)

| 원문 절 | 원문 줄 | 새 위치 | 조치 |
|---|---|---|---|
| 머리말 + 작업 규칙 | 1-14 | 루트 `AGENTS.md`(문서 원칙), `PROJECT_RULES.md`(추적 규칙) | **개정** — "README/CLAUDE/CHANGELOG 만 추적" 규칙을 폐기하고 유지보수 문서·스킬·테스트 추적으로 변경(사유: 에이전트 공통 규칙과 설계/검증 명세가 저장소에 있어야 두 에이전트가 같은 기준을 본다) |
| 디렉토리 구조 | 15-110 | overview.md | 이관 + 정정(누락 파일) |
| 현재 주요 구조 요약 | 111-198 | overview.md | 이관(주제가 섞여 있으나 한 목록으로 유지; 분할 시 누락 위험) |
| 주요 변수명 / 필드명 정리 (설정값·config·DB 컬럼·파싱 키·반환 키·모바일 필드·category 전파) | 199-307 | data-contracts.md | 이관 + 정정(`[MAP]` 누락, 반환 키 추가분) |
| DB 테이블 | 308-371 | data-contracts.md | 이관 |
| 크롤링 파이프라인 (start.py) | 372-412 | crawling-and-processing.md | 이관 |
| 설정 (config.ini / settings.py) | 413-427 | data-contracts.md | 이관 + 정정(`crawl_type` 값, `auto_export_sheet` 기본값) |
| 로그 시스템 | 428-438 | overview.md | 이관 + 정정(별점 WS 로그 경로) |
| WebSocket 이벤트 | 439-468 | data-contracts.md | 이관 |
| 모바일 알림 파이프라인 | 469-501 | data-contracts.md | 이관 |
| 파싱 규칙 | 502-515 | crawling-and-processing.md | 이관 |
| 웹 대시보드 카드 → 상세 URL | 516-535 | web-ui.md | 이관 + 정정(없는 주정차 카드, 서버측 필터) |
| 모바일 API 엔드포인트 | 536-570 | data-contracts.md | 이관(표 형식 깨짐 기록) |
| 주요 아키텍처 결정: 멀티모드/리소스 경로/DB 자동 마이그레이션 | 571-583 | overview.md | 이관 |
| 〃 API 크롤러/별점 2단계/Watchlist | 584-594 | crawling-and-processing.md | 이관 |
| 〃 웹 사이드바/세션 인증/리버스 프록시 | 595-615 | web-ui.md | 이관 |
| 〃 주정차 카테고리/첨부 URL/URL 구분자 | 616-637 | crawling-and-processing.md | 이관 |
| 〃 웹 통계 탭/agencyExact/상세검색/통계 상세검색/첨부 인라인 미디어 | 638-664 | web-ui.md | 이관 + 정정(법규는 사이드바, 차트 없음) |
| 〃 ARM64 빌드 | 665-672 | overview.md | 이관 |
| 빌드 | 673-682 | overview.md | 이관 |
| 디버그 extractor | 683-709 | crawling-and-processing.md | 이관 |
| 크롬 확장 연동 | 710-722 | data-contracts.md | 이관 |
| 변경 이력 | 723-725 | 루트 `AGENTS.md` 문서 원칙 (`CHANGELOG.md`) | 통합 |

분할 스크립트는 원문 15~722행의 비어 있지 않은 모든 줄(`---` 구분선 제외)이 네 주제 문서 중 하나에
같은 문자열로 들어갔는지 검사했다: 미포함 0줄. `legacy-claude-reference.md` 는 원문과 `cmp` 동일(머리 주석 2줄 제외).

## 다른 문서의 위치

| 문서 | 상태 | 비고 |
|---|---|---|
| `docs/status-layer-redesign-plan-2026-05.md` | **역사 문서**(추적 유지) | 맨 위 "구현 메모(2026-05-13)" 가 현행 사실: `상태=raw`, `처리상태=canonical`, `종결여부/보완_미응답=lifecycle`. 그 아래 "현재 진단/계획" 은 구현 전 진단이며 현행 버그 목록이 아니다. 현행 규칙은 `PROJECT_RULES.md` 데이터 의미 절과 `docs/design/statistics-spec.md` 를 따른다 |
| `docs/refactor-hardening-test-plan-2026-05.md` | 메인 checkout 로컬 전용(미추적) | 사용자 로컬 계획 메모. 이번 개편에서 옮기지 않았다 |
| `docs/design/*`, `docs/development/*`, `docs/testing/*` | 2026-09-24 신설 | 웹UI 리뉴얼 설계·실행 환경·검증 명세 |
