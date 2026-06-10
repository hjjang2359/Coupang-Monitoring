"""
Google Sheets에 '범례' 시트를 생성합니다. 1회만 실행하면 됩니다.
"""

import json
from pathlib import Path
import gspread
from google.oauth2.service_account import Credentials

CONFIG_PATH = Path(__file__).parent / "config.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

LEGEND_ROWS = [
    ["표시값", "의미"],
    ["숫자 (예: 25280)", "내 VIID가 아이템위너 — 해당 판매가(원)"],
    ["숫자 — 파란색 텍스트", "내 VIID가 아이템위너이며 WOW 회원 할인가 적용 중"],
    ["품절", "해당 VIID 품절 상태"],
    ["-", "비위너이나 이슈 아님 (자사 또는 쿠팡 로켓이 위너)"],
    ["이슈", "타사가 아이템위너 → Slack 알림 발송됨"],
    ["오류", "페이지 접근 실패 (봇 차단 등)"],
    ["오류(파싱)", "페이지 구조 변경 등 파싱 실패"],
]


def main():
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    base = CONFIG_PATH.parent
    sa_file = config["service_account_file"]
    if not Path(sa_file).is_absolute():
        sa_file = str(base / sa_file)

    creds = Credentials.from_service_account_file(sa_file, scopes=SCOPES)
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(config["spreadsheet_id"])

    existing = [ws.title for ws in sh.worksheets()]
    if "범례" in existing:
        sh.del_worksheet(sh.worksheet("범례"))
        print("기존 범례 시트 삭제")

    ws = sh.add_worksheet(title="범례", rows=20, cols=5)
    ws.update("A1", LEGEND_ROWS)

    ws.format("A1:B1", {
        "textFormat": {"bold": True},
        "backgroundColor": {"red": 0.85, "green": 0.85, "blue": 0.85},
    })

    sh.batch_update({"requests": [
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 0, "endIndex": 1},
                "properties": {"pixelSize": 200},
                "fields": "pixelSize",
            }
        },
        {
            "updateDimensionProperties": {
                "range": {"sheetId": ws.id, "dimension": "COLUMNS", "startIndex": 1, "endIndex": 2},
                "properties": {"pixelSize": 420},
                "fields": "pixelSize",
            }
        },
    ]})

    print("완료! 스프레드시트에서 '범례' 탭을 확인하세요.")


if __name__ == "__main__":
    main()
