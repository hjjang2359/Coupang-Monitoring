"""
이슈 PID 재모니터링 스크립트

오전 모니터링(main.py) 실행 후, 구글 시트에서 "이슈"로 기록된 PID만
1시간 간격으로 자동 재모니터링합니다.
이슈가 전부 해소되면 자동 종료됩니다.

사용법:
    python 이슈pid실행.py
"""

import io
import json
import logging
import subprocess
import sys
import time
from datetime import date
from pathlib import Path

import gspread
from google.oauth2.service_account import Credentials

BASE_DIR = Path(__file__).parent
CONFIG_PATH = BASE_DIR / "config.json"

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

RECHECK_INTERVAL_MIN = 60   # 재모니터링 간격(분)
LOG_INTERVAL_MIN     = 10   # 대기 중 로그 출력 간격(분)


# ============================================================
# 설정 & 로거
# ============================================================

def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    for key in ["service_account_file", "log_dir"]:
        if key in config and not Path(config[key]).is_absolute():
            config[key] = str(BASE_DIR / config[key])
    return config


def setup_logger(log_dir: str) -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"{date.today().strftime('%Y-%m-%d')}.log"

    logger = logging.getLogger("issue_rerun")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace"))
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ============================================================
# Google Sheets
# ============================================================

def get_worksheet(config: dict) -> gspread.Worksheet:
    creds = Credentials.from_service_account_file(
        config["service_account_file"], scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(config["spreadsheet_id"])
    return sh.worksheet(config["sheet_name"])


def get_issue_pids(ws: gspread.Worksheet) -> list[str]:
    """오늘 날짜 컬럼에서 '이슈' 값인 행의 PID(중복 제거)를 반환합니다."""
    today_str = date.today().strftime("%Y-%m-%d")
    all_values = ws.get_all_values()
    if not all_values:
        return []

    headers = all_values[0]
    if today_str not in headers:
        return []

    col_idx = headers.index(today_str)  # 0-based
    issue_pids = []
    for row in all_values[1:]:
        if len(row) > col_idx and row[col_idx] == "이슈":
            pid = row[2].strip() if len(row) > 2 else ""
            if pid and pid not in issue_pids:
                issue_pids.append(pid)

    return issue_pids


# ============================================================
# 대기
# ============================================================

def wait_with_log(minutes: int, logger: logging.Logger):
    for remaining in range(minutes, 0, -LOG_INTERVAL_MIN):
        logger.info(f"[대기 중] {remaining}분 후 재모니터링 시작...")
        time.sleep(LOG_INTERVAL_MIN * 60)


# ============================================================
# Main
# ============================================================

def main():
    config = load_config()
    logger = setup_logger(config["log_dir"])
    logger.info("===== 이슈 PID 재모니터링 스크립트 시작 =====")

    try:
        ws = get_worksheet(config)
        logger.info("Google Sheets 연결 성공")
    except Exception as e:
        logger.error(f"Google Sheets 연결 실패: {e}")
        sys.exit(1)

    # 초기 이슈 PID 조회
    issue_pids = get_issue_pids(ws)
    if not issue_pids:
        logger.info("현재 시트에 이슈 PID 없음 — 종료")
        sys.exit(0)

    logger.info(f"이슈 PID {len(issue_pids)}개 감지: {issue_pids}")
    logger.info(f"1시간마다 재모니터링을 시작합니다. 이슈가 해소되면 자동 종료됩니다.")

    python_exe = sys.executable
    main_script = str(BASE_DIR / "main.py")
    run_count = 0

    while True:
        run_count += 1
        logger.info(f"===== 재모니터링 #{run_count} 시작 — PID {len(issue_pids)}개: {issue_pids} =====")

        try:
            subprocess.run(
                [python_exe, main_script] + issue_pids,
                cwd=str(BASE_DIR),
            )
        except Exception as e:
            logger.error(f"main.py 실행 오류: {e}")

        # 시트 재조회로 이슈 잔존 여부 확인
        try:
            issue_pids = get_issue_pids(ws)
        except Exception as e:
            logger.warning(f"시트 재조회 실패 ({e}) — 이슈 PID 유지, 다음 라운드에서 재시도")

        if not issue_pids:
            logger.info("===== 이슈 전체 해소 확인 — 재모니터링 종료 =====")
            break

        logger.info(f"이슈 {len(issue_pids)}개 지속 중: {issue_pids}")
        wait_with_log(RECHECK_INTERVAL_MIN, logger)


if __name__ == "__main__":
    main()
