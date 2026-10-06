# community-client 규칙 (D2-10, 2026-10-05)

PC 서버(safetyreport)·모바일(safetyreport-mobile)·중앙 계정(safetyreport-community-auth)이 **클라이언트 쪽에서** 같게 해석해야 하는 규칙이다.
중앙 권한 판정(누가 들어갈 수 있는가)은 중앙이 하고, 이 폴더는 그 응답을 각 앱이 같은 방식으로 읽고 다루는 규칙만 정한다.
프로토콜·액션·응답 모양의 정본은 `contracts/community-ingest/account-api.md`(지도 레포 정본의 사본)이며 이 폴더는 그것을 바꾸지 않는다.

정본 위치: 이 저장소(safetyreport) `contracts/community-client/`. 사본: 모바일 `contracts/community-client/`, auth `tests/contracts/community-client/`.
사본을 고치지 않는다. 바꿀 때는 이 폴더를 고치고 `MANIFEST.sha256` 을 다시 만든 뒤(`sha256sum vectors/*.json client-rules.md README.md > MANIFEST.sha256`) 사본을 복사한다.
세 레포의 시험이 사본의 해시가 MANIFEST 와 같은지와 벡터 결과를 함께 확인한다.

| 파일 | 내용 | 검사하는 레포 |
|---|---|---|
| `client-rules.md` | status DTO 정규화, 오류 분류·재시도, 기기 이름, 캐시·늦은 응답 | — |
| `vectors/status-dto.json` | status 응답 → 정규화 DTO·게이트 판정 | 서버, 모바일 |
| `vectors/account-errors.json` | HTTP 응답 → 오류 코드·일시 오류 여부·재시도 대기 | 서버, 모바일, auth(중앙이 내는 상태 코드·retryable) |
| `vectors/device-label.json` | 기기 이름 검증(중앙·서버)과 모바일 정리 결과 | 서버, auth, 모바일 |
| `vectors/gate-timing.json` | 캐시 경계·새 작업 전 확인 상한, 늦은 응답 버리기 | 서버, 모바일 |
