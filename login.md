# 안전신문고 직접 로그인 (API 방식) 역공학 결과

Chrome CDP로 실제 로그인 트래픽을 캡처해 분석한 내용.

---

## 로그인 흐름

```
1. GET  /api/v1/common/rsa/getPublicKey   → RSAModulus, RSAExponent (hex)
2. POST /oauth/token                       → access_token (Bearer)
3. GET  /api/v1/portal/mypage/mysafereport → 신고 목록 (Authorization: BEARER <token>)
```

---

## Step 1 — RSA 공개키 조회

```
GET https://www.safetyreport.go.kr/api/v1/common/rsa/getPublicKey
```

**필수 헤더** (없으면 서버가 연결을 끊음, errno 104):
```
Referer: https://www.safetyreport.go.kr/
X-Requested-With: XMLHttpRequest
User-Agent: Mozilla/5.0 ...
Accept: */*
```

**응답 예시**:
```json
{
  "RSAModulus": "b3f2a1...(hex, 256자)",
  "RSAExponent": "010001"
}
```

---

## Step 2 — 토큰 발급

```
POST https://www.safetyreport.go.kr/oauth/token
Content-Type: application/x-www-form-urlencoded; charset=UTF-8
```

**Body** (form-urlencoded):
```
client_id=web
grant_type=password
loginType=1
username=<아이디>
password=<RSA 암호화된 비밀번호, hex>
```

**비밀번호 암호화 방식**:
- 알고리즘: RSA PKCS#1 v1.5 (JSEncrypt 라이브러리)
- 입력: utf-8 인코딩된 평문 비밀번호 바이트
- 키: Step 1의 RSAModulus(hex) + RSAExponent(hex) → 공개키 구성
- 출력: **hex 문자열** (base64 아님)
  - 예: `4e5fc25c6c0799817d2a7a12ca09b481a27981a390d811f0...` (256자)

**응답**:
```json
{
  "access_token": "eyJhbGciOiJSUzI1NiJ9...",
  "token_type": "bearer",
  "expires_in": 3599
}
```

**오류 처리**:
- HTTP 400/401 → 아이디/비밀번호 불일치
- 그 외 → 서버 오류

---

## Step 3 — API 호출

모든 API 요청에 동일한 헤더 + Authorization 필요:

```
Authorization: BEARER <access_token>
Referer: https://www.safetyreport.go.kr/
X-Requested-With: XMLHttpRequest
User-Agent: Mozilla/5.0 ...
```

### 신고 목록

```
GET /api/v1/portal/mypage/mysafereport
  ?startRowNum=1
  &endRowNum=200
  &C_FRM_DATE=2014-01-01
  &C_TO_DATE=<YYYY-MM-DD>
  &state=
  &seachType=tit
  &C_RELATION2=1
  &searchKeyWord=
```

응답 필드: `totalCnt`, `result[]` (C_NO, C_A_TITLE, C_DATE, STTEMNT_NO, ...)

### 신고 상세

```
GET /api/v1/portal/mypage/mysafereport/<C_NO>
```

응답 필드: C_NO, C_A_CONTENTS, C_NOW, answers[], ARR_C_FILES, STTEMNT_IMAGE_URL,
RN_ADRES, SPLMNT_* (보완완료 정보), C_APP_GUBUN_NM (카테고리명)

---

## 카테고리 분류 (C_APP_GUBUN_NM 기준)

| C_APP_GUBUN_NM 포함 문자열 | 내부 category |
|--------------------------|--------------|
| `자동차·교통위반`           | `traffic`    |
| `불법주정차신고`             | `parking`    |
| 그 외                     | `other`      |

신고 본문(C_A_CONTENTS)에도 동일 정보 포함:
```
본 신고는 안전신문고 앱의 <카테고리명> 메뉴로 접수된 신고입니다
```

---

## 주의사항

- **토큰 만료**: `expires_in: 3599` (1시간). 만료 시 HTTP 401 반환 → 재로그인 필요
- **Connection reset (errno 104)**: Referer/X-Requested-With 헤더 없으면 서버가 차단
  - 간헐적으로 발생 → 재시도 로직 필요 (1~2초 간격, 최대 3회)
- **비밀번호 특수문자**: utf-8 bytes → RSA → hex 과정에서 모두 처리됨
- **전각 숫자**: 서버 응답에 ０１２... 형태의 전각 문자 포함 → 반각 변환 필요
