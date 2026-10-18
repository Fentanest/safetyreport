# 2026-10-18 프록시 신뢰·CSRF 재설계 검수 (구현 Astra, 검수 Opus)

명세: [docs/plans/2026-10-18-proxy-trust-redesign.md](../plans/2026-10-18-proxy-trust-redesign.md).
구현: Astra(codex `gpt-6-astra`, workspace-write, worktree `proxy-trust-redesign`). 커밋·푸시는 Opus.

## 계기
운영 205(호스트 cloudflared → Docker 포트 매핑 6819)에서 앱이 보는 peer 가 브리지 게이트웨이 `172.19.0.1` 이고
`trusted_proxies` 는 예전 값 `10.20.10.3` 이라 관리자 POST 전부 403 `{"detail":"csrf_failed","reason":"origin"}`.
설정 저장도 같은 검사에 막혀 화면에서 고칠 수 없었다. 모바일 클라이언트 모드는 `/api/v1`(API 키, CSRF 없음)이라 정상.

## 검수 결과

| 항목 | 결과 | 근거 |
|---|---|---|
| 전체 unittest(브랜치) | passed — 1,298 OK, skip 5 | `.agent-runs/proxy-trust-1018/opus-full-shim.log` |
| 전체 unittest(변경 전 dev, 같은 환경) | passed — 1,282 OK, skip 6(모바일 저장소 상대 경로 없는 조건부 skip 1 추가) | `opus-baseline-shim.log` |
| 브라우저 재현(Chromium·Firefox) | passed | 아래 표 |
| 코드 검토 | 차단 결함 없음 | 아래 |
| Docker·PyInstaller·실제 205·Tailscale | not-run | 배포 때 확인 |

전체 테스트 환경 주의: 이 PC 의 `/usr/bin/python3.14`(3.14.4)와 apt 로 갱신된 `_asyncio` 확장 모듈이 맞지 않아 TestClient 를 쓰는
테스트가 브랜치·dev 모두 세그폴트(exit 139)한다. 제품과 무관한 환경 결함이며, `sys.modules["_asyncio"] = None` 만 넣은
`sitecustomize`(PYTHONPATH, 저장소 밖)로 순수 Python asyncio 를 써서 두 쪽을 같은 조건으로 돌렸다. 공용 Python 은 바꾸지 않았다.

### 브라우저 재현
fixture 서버 두 개(브랜치·dev) 앞에 Host 를 upstream 으로 바꾸고 `X-Forwarded-Proto: https`·`X-Forwarded-Host` 를 붙이는
테스트 프록시를 두고, 로그인한 화면에서 같은 출처 JSON POST(`/settings/community/cancel`, 게이트 예외 경로)를 보냈다.

| 브라우저 | 대상 | 올바른 토큰 | 틀린 토큰 |
|---|---|---|---|
| Chromium | dev(변경 전) | 403 `reason: origin` (205 증상 재현) | 403 `origin` |
| Chromium | 브랜치 | 409 `no_pending`(라우트 응답 = CSRF 통과) | 403 `csrf` |
| Firefox | dev(변경 전) | 403 `origin` | 403 `origin` |
| Firefox | 브랜치 | 409 `no_pending` | 403 `csrf` |

fixture 는 커뮤니티 필수 게이트 때문에 설정 화면을 열 수 없어 설정 저장·진단 표시는 단위 테스트
(`test_proxy_save_applies_to_next_request_without_recreating_app`, `test_rendered_scripts_are_relative_and_settings_show_socket_peer`)로만 확인했다.

### 코드 검토 메모
- CSRF: `Sec-Fetch-Site` same-origin/none 은 출처 비교 생략, 토큰은 항상 필수. cross-site 거절, same-site/알 수 없는 값은 엄격 비교 유지.
  구형 브라우저용 원래 Host·X-Forwarded-Host 후보는 CSRF 공격자가 정할 수 없는 값이라 peer 신뢰와 무관하게 써도 된다.
- `services/settings_service.py` 의 `instance._lock` 은 RLock 이고 `save()` 도 같은 잠금을 다시 잡으므로 교착 없음.
- 미들웨어 생성 인자 제거에 따른 다른 호출부 없음(`scripts/dev/fixture_server.py` 는 `create_app` 경유).
- 클라이언트 IP 는 표시용(`api_route`, `ws_route`)뿐이고 IP 기반 인증·인가 판단은 없어 사설 대역 기본 신뢰의 영향은 표시 정확도에 그친다.
- 동작 변경: 예전에 `/0` 을 적은 설정은 시작 때 전체 신뢰였으나 이제 경고 후 무시(유효 항목 없으면 auto). 문서 코드 대조 정정에 기록됨.
- Opus 수정: CHANGELOG·web-ui.md 표기(띄어쓰기, 205 증상 설명 일반화).

## 운영 205 적용 메모
새 버전에서는 `trusted_proxies` 를 비우거나 `auto` 로 두면 된다. 예전 값 `10.20.10.3` 이 남아 있어도 브라우저 POST 는 통과하지만
https 판정·접속자 IP 를 맞추려면 설정 화면에서 비우고 저장한다(즉시 반영).
