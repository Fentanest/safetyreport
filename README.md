# 나만의 안전신문고 (My Safety Report Manager)

> 안전신문고에 신고한 내역을 내 PC나 서버로 자동 수집하고, 강력한 검색·분석·자동화 기능을 제공하는 개인용 통합 관리 시스템입니다.

[![GitHub Release](https://img.shields.io/github/v/release/Fentanest/safetyreport)](https://github.com/Fentanest/safetyreport/releases)
[![GitHub Container Registry](https://img.shields.io/badge/Docker-ghcr.io-blue)](https://github.com/Fentanest/safetyreport/pkgs/container/safetyreport)

---

## 이런 분께 필요합니다

- 안전신문고 신고를 수십~수백 건씩 관리하는데 웹사이트가 너무 불편하신 분
- 과태료·범칙금 수용 현황을 기관별·담당자별로 통계로 보고 싶은 분
- 신고가 처리될 때마다 하나하나 만족도 조사를 제출하기 번거로우신 분
- 신고 데이터를 엑셀이나 구글 스프레드시트로 정리하고 싶은 분

---

## 주요 기능

### 자동 데이터 수집 (크롤링)
크롬 브라우저를 이용해 안전신문고에 로그인하고, 신고 목록과 상세 내용(신고명, 신고일, 처리기관, 담당자, 처리상태, 과태료·범칙금, 신고내용, 처리내용, 첨부사진 등)을 자동으로 수집합니다.
- **전체/증분 수집**: 처음엔 전체를 가져오고, 이후엔 새로 생긴 내역만 빠르게 업데이트
- **자동 스케줄러**: 매일 특정 시각 또는 N시간마다 자동 실행 설정 가능
- **실시간 로그**: 크롤링 진행 상황을 웹 화면에서 실시간으로 확인

### 강력한 데이터 조회 및 검색
안전신문고 웹사이트보다 훨씬 다양한 조건으로 검색할 수 있습니다.
- 차량번호, 신고번호, 신고명, 위반법규, 담당자, 처리기관, 위반장소, 처리상태 등 **다중 조건 동시 검색**
- 신고일·발생일·답변일·발생시각 **날짜 및 시간 범위 필터**
- 경찰기관 포함/제외 필터, 취하 데이터 숨기기
- 검색된 결과만 엑셀로 내보내기, 신고번호 일괄 복사

### 처리 부서 통계 분석
내 신고들을 어느 기관이 어떻게 처리했는지 숫자로 확인합니다.
- 기관별·담당자별 수용률, 과태료 부과율, 평균 처리 소요일 분석
- 통합 대시보드에서 전체 현황 (총 신고 수, 처리상태별 분포, 과태료 합계 등) 한눈에 파악

### 자동 만족도 조사 (별점 매크로)
처리가 완료된 신고 건들에 대해 만족도 조사를 자동으로 제출합니다.
- 1~5점 별점을 선택한 뒤 수십~수백 건을 한 번에 처리
- 표에서 원하는 행을 클릭하거나 신고번호를 직접 입력해 대상 지정
- 처리중·취하 등 만족도 조사가 불가능한 건은 자동으로 제외

### 감시 목록 (Watchlist)
특별히 주시해야 할 신고나 차량을 등록해 두면 변경 사항을 빠르게 추적할 수 있습니다.

### 내보내기 연동
- **Excel**: 현재 검색 결과 또는 전체 데이터를 즉시 엑셀 파일로 저장
- **구글 스프레드시트**: Google API 연동으로 클라우드에 자동 업로드 (사진 링크 포함)

### 텔레그램 봇 알림
- 크롤링 완료·오류 발생 시 텔레그램 메시지로 즉시 알림
- 채팅 창에서 명령어로 크롤링 시작·상태 조회 가능

---

## 설치 및 시작 방법

### 방법 1. Docker (가장 간단, 서버/NAS 환경 추천)

**사전 조건**: Docker 및 Docker Compose 설치 필요

`docker-compose.yml` 파일을 생성하고 아래 내용을 붙여넣기 합니다.

```yaml
services:
  mysafetyreport:
    container_name: safetyreport
    image: ghcr.io/fentanest/safetyreport:latest
    ports:
      - 6819:6819
    environment:
      - TZ=Asia/Seoul
    volumes:
      - ./data:/app/data
    restart: unless-stopped

  selenium-hub:
    image: selenium/hub:latest
    container_name: selenium-hub
    ports:
      - 4444:4444
    restart: always

  chrome:
    container_name: chrome
    image: selenium/node-chrome:latest
    platform: linux/amd64
    shm_size: 2gb
    depends_on:
      - selenium-hub
    environment:
      - SE_EVENT_BUS_HOST=selenium-hub
      - SE_JAVA_OPTS=-Xmx512m
    restart: always
```

```bash
docker-compose up -d
```

브라우저에서 `http://서버IP:6819` 으로 접속합니다.
설정 → 크롬 구동 방식을 **Selenium Hub**로 선택하고 주소 `http://selenium-hub:4444/wd/hub` 를 입력하세요.

---

### 방법 2. Windows 실행 파일 (일반 PC 사용자 추천)

**사전 조건**: 크롬(Chrome) 브라우저 설치 필요

1. [릴리즈 페이지](https://github.com/Fentanest/safetyreport/releases)에서 최신 `mysafetyreport-win.zip` 다운로드
2. 압축 해제 후 `mysafetyreport.exe` 실행
3. 자동으로 브라우저가 열리며 웹 UI로 접속됩니다 (`http://127.0.0.1:6819`)
4. 설정 → 크롬 구동 방식을 **로컬 데스크톱 크롬**으로 선택

---

### 방법 3. Linux 실행 파일 (데비안/우분투 서버)

**사전 조건**: 크롬 또는 Selenium Hub 필요

1. [릴리즈 페이지](https://github.com/Fentanest/safetyreport/releases)에서 최신 `mysafetyreport-linux.zip` 다운로드
2. 압축 해제 후 실행 권한 부여 및 실행:
   ```bash
   chmod +x run.sh
   ./run.sh
   ```
3. 브라우저에서 `http://localhost:6819` 으로 접속

---

## 초기 설정

처음 접속하면 관리자 계정 생성 화면이 나타납니다. 아이디와 비밀번호를 설정하면 바로 사용할 수 있습니다.

이후 **설정** 메뉴에서 아래 항목들을 입력하세요.

| 항목 | 설명 |
|------|------|
| 안전신문고 아이디/비밀번호 | 크롤링에 사용할 안전신문고 로그인 계정 |
| 크롬 구동 방식 | 환경에 맞게 선택 (데스크톱 / Selenium Hub / 원격 디버깅) |
| Telegram Bot Token / Chat ID | 선택사항. 텔레그램 알림 사용 시 입력 |
| 구글 스프레드시트 URL | 선택사항. 구글 시트 연동 시 입력 |
| 휴대폰 번호 | 선택사항. 별점 매크로 사용 시 필수 |

---

## 자주 묻는 질문

**Q. 크롤링하면 안전신문고 계정이 차단되지 않나요?**
A. 사람이 직접 클릭하는 것과 동일한 방식으로 동작하며, 요청 사이에 자연스러운 대기 시간이 포함되어 있습니다. 그러나 과도하게 짧은 간격으로 반복 실행하는 것은 권장하지 않습니다.

**Q. 기존 데이터는 보존되나요?**
A. 모든 데이터는 `data/` 폴더의 SQLite 파일에 저장됩니다. Docker 환경에서는 볼륨 마운트(`./data:/app/data`)를 통해 컨테이너를 재시작하거나 업데이트해도 데이터가 유지됩니다.

**Q. 구글 스프레드시트 연동은 어떻게 하나요?**
A. Google Cloud Console에서 서비스 계정을 생성하고 `Service Account JSON` 키 파일을 발급받아 설정 페이지에서 업로드하면 됩니다. 연동할 스프레드시트에 서비스 계정 이메일을 편집자로 공유해야 합니다.

---

## 면책 조항

본 프로그램은 개인적인 데이터 관리 및 분석을 위한 도구입니다. 안전신문고 서비스 이용 약관을 준수하여 사용하시기 바라며, 과도한 요청으로 인한 서비스 제한 등 모든 사용 결과에 대한 책임은 사용자 본인에게 있습니다.
