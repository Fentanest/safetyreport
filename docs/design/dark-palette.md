# 다크 팔레트 — B안 "딥 다크" (2026-09-25)

사용자 결정: 예전 다크가 사실상 "블루 모드"(웹 바탕 `#060e22`, 채도 70%)라 채도 거의 없는 검정 계단으로 바꾼다. **웹과 모바일은 같은 값**을 쓴다.
기계 판독 정본: `contracts/dark-palette.json`(두 레포 바이트 동일). 서버 `tests/test_dark_palette.py`, 모바일 `test/theme/dark_palette_contract_test.dart` 가 확인한다. 라이트 모드는 그대로.

| 역할 | 값 | 웹 토큰(`web/static/ui/tokens.css`) | 모바일(`SrColors.dark` / `AppTheme`) | 대비 |
|---|---|---|---|---|
| 바탕 | `#0b0b0c` | `--sr-bg` | `background` | — |
| 표면(카드) | `#131314` | `--sr-surface` | `surface` | — |
| 표면 2 | `#1b1b1c` | `--sr-surface-2` | `surfaceAlt` | — |
| 표면 3 | `#232324` | `--sr-surface-3` | `ColorScheme.surfaceContainerHigh` | — |
| 테두리 | `#2d2d2f` | `--sr-border` | `border` | — |
| 본문 글자 | `#f3f3f4` | `--sr-text` | `textPrimary` | 바탕 17.7, 표면3 14.2 |
| 보조 글자 | `#9ea0a4` | `--sr-text-muted` | `textSecondary` | 바탕 7.5, 표면3 6.0 |
| 강조 글자·아이콘 | `#60a5fa` | `--sr-primary`(다크) | `ColorScheme.primary` | 바탕 7.7, 표면3 6.2 |
| 채움(버튼·선택 탭, 흰 글자) | `#2563eb` | `--sr-primary-fill` | `AppTheme.darkPrimaryFill`, `SrColors.brand`, FilledButton·TabBar | 흰 글자 5.17 |

- 역할 분리: 예전 웹은 `--sr-primary`(#3b82f6) 하나로 글자와 채움을 다 해서 채운 버튼의 흰 글자 대비가 3.1~3.7 이었다. 이제 글자·장식은 `--sr-primary`, 흰 글자를 올리는 채움은 `--sr-primary-fill`. 라이트에서는 둘 다 `#1f6feb`.
- 사이드바(웹 전용): 바탕 `#050505`, 글자 `#e2e2e4`, 보조 `#84868a`, 선택 `#1e1e20`.
- 상태색(수용·불수용·보완 등)과 지도 위 팝업·마커(테마 무관, `report_map.html` 주석)는 바꾸지 않았다.
- 예전 네이비 다크 참고 이미지 `docs/design/reference/02-pc-dark.png` 는 대체됨.
