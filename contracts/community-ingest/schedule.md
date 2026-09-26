# 자정 업로드 스케줄 v1

- 기준: Asia/Seoul 00:00. 한국은 DST 가 없어 `KST = UTC+9` 고정으로 계산한다(OS·컨테이너 timezone 과 무관).
- `kst_date(t)` = (t + 9h) 의 UTC 날짜. `due_key(t)` = `"midnight:" + kst_date(t)` — t 시점에 이미 지난 가장 최근 KST 자정의 키.
- `due_at(key)` = 그 KST 날짜 00:00 = UTC 전날 15:00. `next_due_at(t)` = t 보다 **엄격히 늦은** 다음 KST 자정.
- 실행 판단 `should_run(t, runs)`: `k = due_key(t)`. `runs[k].state == "succeeded"` 면 false. `runs[k].state == "running"` 이고 lease 가 t 이후까지 유효하면 false(진행 중 run 에 합류). 그 밖은 true.
  **이전 날짜들의 누락은 따로 실행하지 않는다** — 최신 키 하나만 실행해도 journal 의 미ACK 전체를 보내므로 같은 사본을 일수만큼 반복하지 않는다.
- 설치 직후 첫 실행: 오늘 키가 아직 없으면 한 번 실행(보낼 것이 없으면 `no_change` 로 succeeded).
- 기록 필드 구분: `last_scheduled_attempt`(시작 시각), `last_success`(succeeded 로 끝난 시각), `last_due_processed`(succeeded 된 키), `next_due`(next_due_at), `deferred_reason`(auth_required·consent_required·offline·busy·os_deferred·rate_limited).
  시작만으로 succeeded 를 찍지 않는다. partial·실패·인증 필요는 succeeded 가 아니다.
- 시계 역행: t 가 마지막 기록보다 이르면 키 계산은 그대로 하되, 이미 succeeded 인 키는 다시 실행하지 않는다.
- 트리거: PC 는 APScheduler cron(00:00, tz Asia/Seoul) + 서버 시작 시 should_run 확인. Android 는 Workmanager(1시간 주기 periodic + 다음 자정 one-off) + 앱 resume. iOS 는 BGTask(earliestBeginDate 는 가장 이른 시각일 뿐) + resume. 모두 같은 should_run·같은 lease.
