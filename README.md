# 쿠팡 아이템위너 모니터링

Google Sheets에 등록된 SKU(VIID)가 쿠팡 아이템위너인지 매일 자동으로 확인하고, 이슈 발생 시 Slack으로 알림을 보냅니다.

---

## 파일 구조

```
쿠팡아이템위너모니터링/
├── main.py                  # 핵심 실행 스크립트
├── config.json              # 설정 파일 (git 제외)
├── service_account.json     # Google 서비스 계정 키 — 직접 배치 필요 (git 제외)
├── viid_map.json            # VIID → itemId 로컬 캐시 (자동 생성)
├── 이슈로그.csv              # 이슈 이력 누적 파일 (자동 생성)
├── run.bat                  # 모니터링 실행
├── 지정PID실행.bat           # 특정 PID만 테스트할 때 사용
├── 페이지검사.bat            # 페이지 구조 확인용
├── chrome_profile/          # Chrome 전용 프로필 (자동 생성, 봇 차단 방지용)
├── logs/                    # 일별 실행 로그 (자동 생성)
├── inspect_page.py          # 페이지 파싱 디버그용
└── analyze_price.py         # 가격 구조 분석용
```

> `service_account.json`, `config.json`은 보안 정보가 포함되어 있으므로 git에 커밋하지 않습니다.

---

## 설정 (config.json)

```json
{
  "spreadsheet_id": "Google Sheets 문서 ID",
  "sheet_name": "시트 이름",
  "service_account_file": "service_account.json",
  "slack_webhook_url": "Slack 웹훅 URL",
  "request_delay_seconds": 8,
  "request_timeout_seconds": 15,
  "log_dir": "logs",
  "nas_log_path": "NAS 경로 (선택, 비워두면 미사용)",
  "allowed_winner_sellers": ["이삼오구", "쿠팡"],
  "cookie_file": "",
  "chrome_profile_dir": ""
}
```

| 항목 | 설명 |
|------|------|
| `spreadsheet_id` | 모니터링 대상 Google Sheets 문서 ID |
| `sheet_name` | 데이터가 있는 시트 이름 |
| `request_delay_seconds` | 페이지 요청 간격(초). 봇 차단 방지용 |
| `allowed_winner_sellers` | 이 판매자가 위너여도 이슈로 취급하지 않음 |
| `cookie_file` | 쿠키 파일 경로 (비워두면 사용 안 함) |
| `chrome_profile_dir` | Chrome 프로필 경로 (비워두면 `chrome_profile/` 자동 사용) |

---

## Google Sheets 시트 구조

| A열 | B열 | C열 | D열 | E열~ |
|-----|-----|-----|-----|------|
| 브랜드명 | SKU명 | PID | VIID | 날짜별 결과 (자동 기입) |

- 1행: 헤더 (D열까지) + 실행 날짜 (`2026-05-14` 형식, 자동 생성)
- 2행~: 모니터링 대상 SKU

---

## 실행 흐름

```
run.bat 실행
    │
    ▼
main.py 시작
    │
    ├─ config.json 로드
    ├─ viid_map.json 로드 (VIID→itemId 로컬 캐시)
    ├─ Google Sheets 연결 → 시트 데이터 로드
    ├─ 오늘 날짜 컬럼 확인 (없으면 자동 생성)
    ├─ Chrome 전용 프로필로 브라우저 실행 (chrome_profile/ 자동 생성)
    │
    ├─ [재실행 감지]
    │   ├─ 오늘 컬럼에 이미 기록된 PID → 건너뜀 (resume)
    │   └─ 모든 PID가 이미 완료 → 새 회차로 전체 재처리
    │
    ▼
PID별 그룹핑 후 순차 처리
    │
    ├─ Chrome으로 쿠팡 상품 페이지 접속
    │   ├─ 봇 차단 감지 시 → 60초 대기 후 자동 재시도 (팝업 없음)
    │   └─ 브라우저 종료 감지 시 → 브라우저 재시작 후 재시도
    │
    ├─ 페이지 파싱
    │   ├─ 위너 VIID 목록 추출 (parse_winners)
    │   ├─ 품절 VIID 목록 추출 (parse_sold_out_viids)
    │   └─ 전체 VIID→itemId 매핑 갱신 (parse_all_viid_items)
    │
    ├─ VIID별 판정
    │   ├─ 품절 → "품절"
    │   ├─ 내 VIID가 위너 → 판매가(숫자) 기록
    │   └─ 내 VIID가 비위너
    │       ├─ itemId로 같은 옵션의 위너 VIID 파악
    │       ├─ 위너 VIID 페이지 접속 → 실제 판매자 + 활성 여부 확인
    │       │   └─ 동일 위너 VIID는 캐시 활용 (중복 요청 방지)
    │       ├─ 위너 VIID 활성 O
    │       │   ├─ 위너 판매자가 allowed_winner_sellers에 포함 → "-" (이슈 아님)
    │       │   └─ 위너 판매자가 타사 → 판매가(빨간색) + Slack 알림
    │       └─ 위너 VIID 비활성 (판매중단 상품이 위너로 캐싱된 케이스)
    │           └─ 내 VIID 페이지 직접 접속 → 실제 위너 재확인
    │               ├─ 내가 위너 → 판매가(숫자) 기록
    │               ├─ 위너 판매자 확인 → "-" or 판매가(빨간색)
    │               └─ 위너 없음 → "-"
    │
    └─ PID 처리 완료 즉시 Google Sheets 기록 (PID별 실시간 업데이트)
    │
    ▼
완료
    ├─ 이슈로그.csv 누적 저장
    ├─ Slack 알림 발송 (이슈 건수 > 0일 때)
    └─ NAS 로그 업로드 (설정된 경우)
```

---

## 결과 값 의미

| 셀 값 | 색상 | 의미 |
|-------|------|------|
| 숫자 (예: 25280) | 검정 | 내 VIID가 아이템위너, 해당 판매가 |
| 품절 | 회색 (#999999) | 해당 VIID 품절 상태 |
| - | 검정 | 비위너이나 이슈 아님 (위너가 allowed_winner_sellers 소속) |
| 숫자 (이슈) | 빨간 | 타사가 아이템위너 → Slack 알림 발송 |
| 오류 | 검정 | 페이지 파싱 실패 또는 봇 차단 |

---

## allowed_winner_sellers 동작 방식

비위너로 판정되면 해당 옵션의 **위너 VIID 페이지에 별도 접속**해 실제 판매자명을 확인합니다.  
페이지 기본 선택 옵션 기준이 아닌 **옵션별 위너 판매자**를 정확히 파악합니다.

- `"이삼오구"` 포함 → 자사 다른 VIID가 위너 → `-`
- `"쿠팡"` → 쿠팡 로켓배송이 위너 (단위가격 알고리즘 등) → `-`
- 그 외 타사 → 판매가 **빨간색** + Slack 알림

`config.json`의 `allowed_winner_sellers` 배열에서 추가/제거 가능합니다.

---

## 봇 차단 대응

쿠팡의 Akamai WAF에 의해 봇으로 감지되면:

1. **자동 60초 대기** 후 세션 리셋 → 재시도 (팝업 없음, 사람이 개입할 필요 없음)
2. 브라우저가 완전히 종료된 경우 → 브라우저 자동 재시작 후 재시도

IP 차단(Access Denied + `errors.edgesuite.net`)이 발생한 경우에는 일정 시간 경과 후 재실행해야 합니다.

---

## 재실행 시 동작 (Resume)

중간에 중단된 경우 그냥 `run.bat`을 다시 실행하면 됩니다.

- Google Sheets의 오늘 컬럼에 이미 값이 기록된 PID는 **자동으로 건너뜀**
- 미처리된 PID부터 이어서 진행
- 모든 PID가 이미 완료된 경우(새 회차) → **전체 재처리**

---

## Chrome 전용 프로필 (chrome_profile/)

봇 차단 방지를 위해 모니터링 전용 Chrome 프로필을 사용합니다.

- 최초 실행 시 자동 생성 (`chrome_profile/` 폴더)
- 쿠키, 캐시, 브라우저 히스토리가 누적되어 일반 사용자처럼 인식됨
- 다른 PC로 이전할 경우 이 폴더도 함께 복사하면 효과 유지

---

## VIID → itemId 캐시 (viid_map.json)

같은 itemId를 가진 VIID들은 동일 옵션입니다. 비위너 VIID의 itemId를 모를 경우 해당 VIID URL로 추가 요청해 확인하며, 확인된 값은 `viid_map.json`에 저장해 이후 실행에서 재사용합니다.

---

## 로그

- `logs/YYYY-MM-DD.log` — 일별 실행 로그
- `이슈로그.csv` — 이슈 발생 이력 전체 누적

---

## 다른 PC에서 실행하기

1. 이 폴더 전체를 복사
2. `service_account.json` 파일 배치 (Google 서비스 계정 키)
3. `config.json` 내용 확인 (spreadsheet_id, slack_webhook_url 등)
4. Python 및 아래 패키지 설치:
   ```
   pip install undetected-chromedriver gspread google-auth beautifulsoup4 requests
   ```
5. `run.bat` 실행

> `chrome_profile/` 폴더도 함께 복사하면 프로필 히스토리가 이어집니다.

---

## 의존 패키지

```
undetected-chromedriver
gspread
google-auth
beautifulsoup4
requests
```
