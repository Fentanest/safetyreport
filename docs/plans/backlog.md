# 백로그 (safetyreport)

아직 하지 않은 일. 끝나면 이 표에서 지우고 `CHANGELOG.md` 에 기록한다. 상세 설계·근거는 링크한 문서를 따른다.

| ID | 항목 | 상태 | 언제 |
|---|---|---|---|
| BL-1 | 공유 자료 전체 삭제 후 사진 백필·추정 과태료 재전송이 삭제 표시를 되살림 | 대기 (지금은 영향 없음) | 공유 자료 전체 삭제를 동의 철회/카카오 로그아웃 흐름으로 구현할 때 |

## BL-1 공유 자료 전체 삭제 후 보충 캡처가 삭제 표시를 되살림

2026-10-13 등록. 출처: v3.2.0 출시 검수(Astra) P3. 메모리 DB 에서 재현.

### 현상
- `services/community_account_ops.contributions_delete` 로 로컬 관측이 `deleted_by_user` / `blocked` 가 된 뒤,
  새 연결을 설정하고 보충 캡처(`trigger="fine_estimate_backfill"`)가 돌면 삭제 사유가 보존되지 않는다.
  새 revision · `blocked_reason=NULL` · outbox `pending` 이 생겨 지운 자료가 다시 업로드 대기에 오른다.
- 보충 캡처를 부르는 곳:
  - `services/photo_capture_time.py` `fill_one` — 사진 촬영 시각을 채운 직후 (앱 시작 시 한 번 훑기 `main.py` · 크롤링마다 최대 30건)
  - `services/community_capture.py` `resend_fine_estimate_updates` — 앱 업데이트 뒤 한 번
- 재현 위치: `services/community_capture.py` `capture()` 의 backfill 분기(`_backfill_payload`).

### 지금 영향이 없는 이유
- 웹 경로 `/contributions-delete` 는 주석 처리돼 있다(`web/routers/community_route.py`, 2026-09-27 결정).
- 의도한 구현은 **동의 철회 / 카카오 로그아웃** 으로 공유 자료를 지우는 것이다(2026-10-13 사용자 확인).
  로그아웃·철회 상태에서는 동의·계정 문맥이 없어 `capture()` 가 journal 만 쓰고 outbox 에 넣지 않으므로 업로드되지 않는다.

### 할 일 (구현 시)
1. 보충 캡처는 삭제 표시(`deleted_by_user` / `blocked_reason`)가 있는 관측을 건너뛴다 — 새 revision 을 만들지 않는다.
   재연결(새 동의) 뒤에도 같다. 모바일은 같은 문제를 이미 막았다(`lib/community/capture/community_capture.dart` 후보 조회·발급 트랜잭션의 삭제 차단).
2. 회귀 시험: 삭제 → 재연결 → 사진 백필 / 추정 과태료 재전송 → outbox 에 아무것도 없음.
