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

- **세션 쿠키 필수**: Step 1(RSA 키 조회) 응답의 `Set-Cookie: JSESSIONID=...` 를 Step 2(토큰 발급) 요청 헤더에 `Cookie: JSESSIONID=...` 로 전달해야 함.
  서버가 RSA 키를 세션에 바인딩하므로, 쿠키 없이 POST 하면 `"RSA decrypt failed: No installed provider supports this key: (null)"` (HTTP 401) 반환.
- **토큰 만료**: `expires_in: 3599` (정확히 1시간). 만료 시 HTTP 401 반환 → 재로그인 필요.
  JSESSIONID 쿠키는 세션 쿠키(만료 없음, 브라우저 종료 시 소멸) — 로그인 핸드셰이크 이후에는 Bearer 토큰만 사용.
  웹 UI 5시간 세션 유지는 별도 서버 세션 로직으로 추정 (API 토큰과 무관).
- **Connection reset (errno 104)**: Referer/X-Requested-With 헤더 없으면 서버가 차단
  - 간헐적으로 발생 → 재시도 로직 필요 (1~2초 간격, 최대 3회)
- **비밀번호 특수문자**: utf-8 bytes → RSA → hex 과정에서 모두 처리됨
- **전각 숫자**: 서버 응답에 ０１２... 형태의 전각 문자 포함 → 반각 변환 필요
- **TLS 핑거프린팅**: Python `requests` 라이브러리는 TLS ClientHello가 브라우저와 달라 연결이 차단됨. curl 또는 Android Dart http 패키지는 통과.

---

## 모바일 앱 구현 (safetyreport-mobile)

`lib/services/standalone_auth_service.dart`

### 로그인 방식: `dart:io HttpClient` 사용

**왜 `dart:io HttpClient`인가**:  
`package:http`의 `http.get()`/`http.post()` 정적 메서드는 **매 호출마다 별도 클라이언트를 생성**하므로, Step 1에서 받은 JSESSIONID 쿠키가 Step 2로 자동 전달되지 않음.  
`dart:io HttpClient` 인스턴스는 쿠키를 자동으로 관리하므로 JSESSIONID 수동 추출 불필요.

**헤더 구성** (Referer, X-Requested-With, User-Agent 필수):
```dart
const headers = {
  'Referer': 'https://www.safetyreport.go.kr/',
  'X-Requested-With': 'XMLHttpRequest',
  'User-Agent': 'Mozilla/5.0 (Linux; Android 10; Mobile) ...',
  'Accept': 'application/json, text/plain, */*',
};
```

**RSA 암호화** — `pointycastle` 패키지 사용:
```dart
// 비밀번호 → utf8 bytes → PKCS1 v1.5 RSA 암호화 → hex 문자열 (512자)
final modulus = BigInt.parse(rsaModulus, radix: 16);
final exponent = BigInt.parse(rsaExponent, radix: 16);
final pubKey = RSAPublicKey(modulus, exponent);
final cipher = PKCS1Encoding(RSAEngine())
  ..init(true, PublicKeyParameter<RSAPublicKey>(pubKey));
final encrypted = cipher.process(Uint8List.fromList(utf8.encode(password)));
final hexPw = encrypted.map((b) => b.toRadixString(16).padLeft(2, '0')).join();
```

**토큰 발급 POST body** (form-urlencoded, Map 방식으로 직렬화):
```
client_id=web&grant_type=password&loginType=1&username=<id>&password=<hex>
```
→ `Uri(queryParameters: {...}).query` 로 percent-encoding 처리

**전체 흐름**:
```dart
final client = HttpClient();
try {
  // Step 1: RSA 키 조회 — JSESSIONID 쿠키 자동 저장됨
  final keyReq = await client.getUrl(Uri.parse('$base/api/v1/common/rsa/getPublicKey'));
  headers.forEach(keyReq.headers.set);
  final keyRes = await keyReq.close();
  final keyBody = jsonDecode(await keyRes.transform(utf8.decoder).join());
  // RSA 암호화...

  // Step 2: 토큰 발급 — client가 JSESSIONID를 자동으로 Cookie 헤더에 포함
  final tokenReq = await client.postUrl(Uri.parse('$base/oauth/token'));
  tokenReq.headers.contentType =
      ContentType('application', 'x-www-form-urlencoded', charset: 'utf-8');
  headers.forEach(tokenReq.headers.set);
  tokenReq.write('client_id=web&grant_type=password&loginType=1'
      '&username=${Uri.encodeComponent(username)}&password=$hexPw');
  final tokenRes = await tokenReq.close();
  final tokenBody = jsonDecode(await tokenRes.transform(utf8.decoder).join());
  final accessToken = tokenBody['access_token'] as String;
} finally {
  client.close();
}
```

### 이후 API 호출: `package:http` + Authorization 헤더

로그인 이후의 신고 목록/상세 API는 `package:http`를 사용해도 무방  
(쿠키 없이 Bearer 토큰만으로 인증):
```
Authorization: BEARER <access_token>
```

### 자동 재로그인 (토큰 만료 대응)

토큰 수명 `expires_in: 3599`(정확히 1시간). 만료 5분 전부터 무효 처리.

1. **`ensureValidToken()`**: API 호출 전 유효성 확인, 만료 시 `tryAutoRelogin()` 호출
2. **`tryAutoRelogin()`**: `FlutterSecureStorage`의 저장 비밀번호로 자동 재로그인
3. **`_getWithRetry()`** (StandaloneApiService): 401 시 자동 재로그인 후 1회 재시도
4. **`TokenExpiredException`**: 재로그인 실패 시 throw → UI에서 수동 재로그인 안내

### 자격증명 저장

| 항목 | 저장소 | 키 |
|------|--------|-----|
| access_token | SharedPreferences | `standaloneToken` |
| 만료 시각(ms) | SharedPreferences | `standaloneTokenExpiresAt` |
| 비밀번호 | FlutterSecureStorage | `standalone_password` |
| 아이디 | SharedPreferences | `standaloneUsername` |

---

## 파싱 주의사항 (C_A_CONTENTS 포맷 차이)

서버는 Selenium으로 HTML 페이지를 스크랩 시 `get_text(separator='\n')`으로 요소 간 `\n` 추가.  
모바일은 JSON API의 `C_A_CONTENTS` 필드를 직접 파싱.

**구형 신고(2023년 이전)**: `C_A_CONTENTS` 내 항목 구분이 `\n` 없이 `*` 로만 연결됨:
```
* 차량번호 : 69로0470* 발생일자 : 2023.10.05* 발생시각 : 23:54
```
**신형 신고**: `\n`으로 구분:
```
* 차량번호 : 69로0470
* 발생일자 : 2023.10.05
```

→ `standalone_parser.dart`의 차량번호 regex에 `\*` 추가 대응:
```dart
RegExp(r'차량번호\s*:\s*(.*?)(?=\n|\*|\(위|$)')
```

