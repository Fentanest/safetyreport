# PC 동의문 Markdown 표시 검수 — 2026-09-27

## 변경

- `community_consent_card.html`의 문서 표시만 카드 전용 `community_consent_markdown.js`로 분리했다. 중앙 `policy.text`를 받는 경로와 동의 버전·해시·버튼·재시도 흐름은 수정하지 않았다.
- 제목 1~3단계, 문단, 목록, 굵게, 인라인 코드, GitHub 표와 셀 안 인라인 서식을 DOM 요소로 표시한다. 표에 Bootstrap `table table-sm`과 `table-responsive` 래퍼를 쓴다. 알 수 없는 문법은 텍스트로 남긴다.
- 링크는 HTTPS 및 `safemap.worklazy.net`·`safeauth.worklazy.net`·`github.com`의 정확한 호스트만 새 창으로 연다. 다른 URL은 `글 (주소)` 텍스트로 표시한다. 본문 조립에는 `createElement`와 `textContent`만 사용한다.

## 검증

- Node 기반 unittest가 실제 `share-consent-2026-09-28.1.md`를 렌더링해 제목·표·코드·굵게·링크와 원문 Markdown 기호 제거를 확인했다. 별도 입력으로 목록, 셀 서식, 비허용 링크와 HTML 문자열의 텍스트 표시를 확인했다.
- 웹 계약 인벤토리 변경 전후 JSON은 바이트 단위로 같았다: `.agent-runs/consent-markdown/inventory-before.json`, `inventory-after.json`.
- fixture 서버는 이 샌드박스에서 `127.0.0.1` 포트 바인딩에 실패했다. 로컬 Chrome headless도 crashpad 소켓 권한 오류로 종료돼 실제 화면 캡처는 만들지 못했다. 두 시도 로그와 캡처 스크립트는 `.agent-runs/consent-markdown/`에 있다.
- 요청한 전체 unittest(발견된 테스트 496개)는 `test_community_rebuild.RouterTest.test_api_status_and_start`에서 진행이 멈춰 중단했다. 탐색용으로 해당 테스트를 제외하거나 `test_community_rebuild` 파일을 제외한 실행도 끝나지 않았다. 후자는 커뮤니티 게이트 테스트에서 오류를 보였고 35초 제한 시간에 종료됐다. 전체 스위트 통과 여부는 확인하지 못했다. 로그: `.agent-runs/consent-markdown/unittest*.log`.

렌더러 단독 unittest: 1개 통과. 전체 스위트: blocked.

## 통합자(community-map 세션) 후속 조정
- Sol 실행 환경에서 전체 unittest·화면 캡처가 막혀(소켓·포트 제한) 통합자가 실행했다.
- 동의문 사본이 dev의 계약 사본에서 빠졌으므로(중앙 `policy`로만 받음) JS 검사는 `tests/fixtures/share-consent-2026-09-28.1.txt`(지도 레포 `contracts/consent/` 정본 사본)를 읽는다.
- 스크립트 태그에 다른 정적 파일처럼 `?v={{ request.state.app_version }}`를 붙였다(업데이트 뒤 옛 캐시 방지).
- 증거: `docs/reviews/screenshots/consent-markdown/pc-1280-light.png`, `pc-1280-dark.png`, `pc-390-light.png` — 실제 `community_consent_markdown.js`와 Bootstrap 5.3.0·`web/static/ui/{tokens,theme}.css`로 `#ccDoc`만 렌더(headless Chrome). 세 폭 모두 원문 마크다운 기호 없음, 페이지 가로 넘침 0, 링크 4개 모두 `_blank`·`noopener noreferrer`.
