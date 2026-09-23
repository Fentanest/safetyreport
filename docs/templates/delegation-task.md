# Gemini 작업서

- task_id:
- role: implementation | review
- 지정 모델/실행자: agy 1.2.9 `--model gemini-3.1-pro-high` (docs/agent-dispatch-runbook.md §3)
- base_sha / 현재 branch / worktree:
- 목표와 사용자 승인 범위:
- owned_files (수정 소유권):
- related_changes (연관 서비스/테스트 수정 허용):
- 공통 파일 소유자/경계:
- 참고 이미지와 명세:
- fixture 데이터 루트 / 서버 기동 방법 / 포트:
- 브라우저 프로필 또는 named session:
- 허용된 Docker context/project/volume:
- 불허 외부 부작용:
- 요구 테스트와 스크린샷:
- 산출물 경로 (.agent-runs/<task_id>/):
- 인용 검증 수단: `python3 scripts/dev/web_contract_inventory.py --check-ids …`, 인벤토리 JSON 경로
- 완료 판정:

## 권한
저장소 전체 조사, 작업·연관 코드 수정, 셸, Python/Node 개발 도구, 브라우저, localhost,
할당된 테스트 Docker 사용을 허용한다. read-only 자문으로 축소하지 않는다.
운영 데이터/실계정/실제 전송/배포는 별도 승인 대상이다.

## 결과 형식
변경 파일 / 동작과 디자인 근거 / 테스트 command와 실제 결과 / 실제 화면 경로 /
권한 거부·환경 장애 / 미검증 항목 / integration notes / commit 또는 diff.
review 역할은 자신의 구현에 독립 승인하지 않는다.
