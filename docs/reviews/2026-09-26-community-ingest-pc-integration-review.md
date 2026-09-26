# 2026-09-26 커뮤니티 공유 PC 통합 검수 (T3a·T3b·T4)

## 실행
- 제미나이 실제 호출: `agy --model gemini-3.1-pro-high --effort high`(`.agent-runs/ci-20260926/gemini-review-pc`, exit 0, report.md). 읽기 전용 검토, T4·T3b diff 대 계약.
- Opus 판정은 실행 증거로 했다: PC 단위 전체, 실제 로컬 합성 스택(ci0926-int, 실제 GoTrue·PostgREST·Postgres 17·edge-runtime, 가짜 카카오만) 수직 테스트.

## 제미나이 지적과 판정
| 지적 | 판정 | 조치 |
|---|---|---|
| `community_upload_route._check_client_user` 토큰 누락·import 실패 시 통과(fail-open) | 맞음(높음) | 통합 커밋 a138a4d: 업로드 실행은 `X-Community-User-Token` 필수, 불일치·오류는 거부 |
| `community_rebuild._gate_fresh` import 실패 시 `can_enter: True` | 맞음(높음) | a138a4d: 직접 import(fail-closed). 같은 유형 `crawl_control._check_crawl_allowed`·`community_uploader._gate_check`·rebuild 라우트 토큰 검증도 함께 수정(제미나이 미지적) |
| `refresh_server_completed` upload lease 미보유 | 맞음(중간) | 이번 커밋: 받는 동안 lease, 다른 업로드가 잡고 있으면 교체 안 함 |
| `capture` 시그니처가 interfaces.md 와 다름 | **오탐** | 실제 `capture(adapter_input, *, source_report_id, trigger, rebuild_run_id, data_dir)` 로 계약과 같다 |

## 제미나이가 놓친 결함(실제 스택 테스트로 발견)
- PC manifest 클라이언트가 계약과 달랐다: 요청에 `protocol`·`connection_id` 없음(서버 400), 응답을 `keys`/`has_more` 로 읽음(계약 `key_prefixes`/`next_after`).
  단위 테스트가 같은 잘못된 모양으로 mock 해 통과하고 있었다. → 계약대로 요청·응답 형식 검증(24hex·10진 토큰·64hex 커서·total)·dataset/epoch 일치·
  total/중복/토큰 3회 규칙으로 재작성, 단위 테스트를 계약 모양으로 교체, `tests/test_community_live_stack.py` 로 실제 스택 확인.
- 병렬 작업용 가짜 모듈 주입(`sys.modules` 만 교체)은 실제 모듈이 import 된 뒤 효과가 없어 T3b 테스트 20건이 통합 후 실패 → 패키지 속성도 함께 교체.

## 결과
- PC 단위 전체: 359 OK(skip 4 = 라이브 테스트 3 + 스택 수직 1).
- 실제 스택 수직(`COMMUNITY_STACK=1`): 1 OK — 로그인만으로는 consent_required, 동의 후 writer 등록·context 활성·manifest 선행, capture → 업로드 ACK accepted/published →
  공개 함수 1건, PC 키 = 서버 `source_report_key` 앞 24hex, manifest 교체, 같은 내용 재관측 이벤트 없음·원장 불변, 원격 철회 → consent_required·context 비활성·
  공개 0·대기 이벤트 미전송.
- 확인 못 함: 실제 카카오(호스팅) 로그인, PyInstaller·Docker 산출물에서의 동작(T8), 브라우저 화면 검수(T7).
