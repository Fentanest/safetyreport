---
name: sr-jinja-theme-renewal
description: Implement the approved navy-blue dark and white themes in the existing FastAPI Jinja2 Bootstrap pages without replacing the stack or breaking DOM contracts.
---

# 승인된 웹 테마 구현

먼저 `docs/design/ui-renewal-spec.md`, `docs/design/pilot-dom-contracts.md`, `docs/design/asset-manifest.csv` 와 대상 템플릿을 읽는다.

1. 시각 정본은 `docs/design/reference/01-pc-light.png`, `02-pc-dark.png` 뿐이다. 03~14 보드와 frontend-design 스킬의 취향은 보조다.
2. 토큰은 `web/static/ui/tokens.css`(CSS custom properties), Bootstrap 연동은 `<html data-bs-theme>`. 선택값(light/dark/system)은
   localStorage 에 두되 접근 실패를 처리하고, 첫 paint 전에 `<head>` 인라인 스크립트로 적용해 깜빡임을 막는다.
3. 하드코딩 색을 정리한다: `.bg-white/.bg-light/.text-dark/.table-light`, 인라인 `style="...color..."`, JS 문자열 안의 클래스
   (base.html 버전 표시·보완 이력·미디어 래퍼, data_table 배지 fallback), `<style>` 블록 hex, flatpickr·DataTables 상태색, 지도 팝업.
4. JS 가 이름으로 교체하는 클래스(stats 의 `btn-primary/warning/success` ↔ `btn-outline-*`, 법규 버튼)는 마크업만 바꾸면 되돌아간다. JS 매핑을 함께 바꾼다.
5. DOM id·class·data-* 계약(`pilot-dom-contracts.md`)과 헤더 텍스트(CSV 컬럼명·통계 열 저장 키)를 유지한다. 바꿔야 하면 계약 표와 테스트를 먼저 고친다.
6. 새 CSS/JS/이미지는 `web/static/ui/` 아래(PyInstaller add-data·Docker 에 자동 포함). 제품 런타임에 Node 를 요구하지 않는다.
7. fixture 서버에서 두 테마를 Playwright 로 캡처하고 장문·좁은 폭·200% 확대·키보드·빈 상태를 확인한다.

산출: 토큰과 적용 코드, 두 모드 실제 캡처, 바뀐 DOM 계약 목록, 테스트 결과. 승인된 컨셉을 새 컨셉으로 바꾸는 제안은 섞지 않는다.
