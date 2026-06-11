"""
쿠팡 아이템위너 모니터링 자동화

실행 방법:
    python main.py

사전 준비:
    1. python -m pip install beautifulsoup4 gspread google-auth undetected-chromedriver
    2. config.json 작성 (spreadsheet_id, sheet_name, slack_webhook_url 등)
    3. service_account.json 배치
"""

import csv
import json
import logging
import re
import shutil
import sys
import time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

import random
import requests
import undetected_chromedriver as uc
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# SECTION 1: Configuration Loading
# ============================================================

CONFIG_PATH = Path(__file__).parent / "config.json"
VIID_MAP_PATH = Path(__file__).parent / "viid_map.json"
ISSUE_LOG_PATH = Path(__file__).parent / "이슈로그.csv"


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        print(f"[오류] config.json을 찾을 수 없습니다: {CONFIG_PATH}")
        sys.exit(1)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)
    # 상대 경로를 프로젝트 폴더 기준 절대 경로로 변환
    base = CONFIG_PATH.parent
    for key in ["service_account_file", "log_dir"]:
        if key in config and not Path(config[key]).is_absolute():
            config[key] = str(base / config[key])
    return config


# ============================================================
# SECTION 2: Logging
# ============================================================

def setup_logger(log_dir: str) -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    log_file = Path(log_dir) / f"{date.today().strftime('%Y-%m-%d')}.log"

    logger = logging.getLogger("monitor")
    logger.setLevel(logging.INFO)

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", "%H:%M:%S")

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    import io
    ch = logging.StreamHandler(io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace"))
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


# ============================================================
# SECTION 3: Google Sheets Client
# ============================================================

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_worksheet(config: dict) -> gspread.Worksheet:
    creds = Credentials.from_service_account_file(
        config["service_account_file"], scopes=SCOPES
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(config["spreadsheet_id"])
    return sh.worksheet(config["sheet_name"])


def load_sheet_rows(ws: gspread.Worksheet) -> tuple:
    all_values = ws.get_all_values()
    if not all_values:
        return [], []
    headers = all_values[0]

    # 헤더 이름으로 열 위치 탐색 (열 순서가 바뀌어도 동작)
    def find_col(name):
        for idx, h in enumerate(headers):
            if h.strip().upper() == name.upper():
                return idx
        raise ValueError(f"시트 1행에서 '{name}' 헤더를 찾을 수 없습니다. 헤더명을 확인해주세요.")

    col_brand   = find_col("BRAND")
    col_sku     = find_col("SKUNAME")
    col_pid     = find_col("ProductID")
    col_viid    = find_col("VIID")

    rows = []
    for i, row in enumerate(all_values[1:], start=2):
        if len(row) <= max(col_brand, col_sku, col_pid, col_viid):
            continue
        brand    = row[col_brand].strip()
        sku_name = row[col_sku].strip()
        pid      = row[col_pid].strip()
        viid     = row[col_viid].strip()
        if not pid or not viid:
            continue
        rows.append({
            "brand":     brand,
            "sku_name":  sku_name,
            "pid":       pid,
            "viid":      viid,
            "row_index": i,
        })
    return rows, headers


def col_index_to_letter(n: int) -> str:
    result = ""
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        result = chr(65 + remainder) + result
    return result


def get_or_create_date_column(ws: gspread.Worksheet, headers: list) -> int:
    today_str = date.today().strftime("%Y-%m-%d")
    for col_idx, header in enumerate(headers, start=1):
        if header == today_str:
            return col_idx
    new_col_idx = len(headers) + 1
    if new_col_idx > ws.col_count:
        ws.add_cols(new_col_idx - ws.col_count)
    col_letter = col_index_to_letter(new_col_idx)
    ws.update(range_name=f"{col_letter}1", values=[[today_str]])
    return new_col_idx


def write_results_batch(ws: gspread.Worksheet, results: list, col_idx: int, logger: logging.Logger):
    col_letter = col_index_to_letter(col_idx)
    updates = [
        {"range": f"{col_letter}{r['row_index']}", "values": [[r["value"]]]}
        for r in results
    ]
    if not updates:
        return

    write_ok = False
    for attempt in range(1, 4):
        try:
            ws.batch_update(updates, value_input_option="USER_ENTERED")
            logger.info(f"Google Sheets 기록 완료 ({len(updates)}개 셀)")
            write_ok = True
            break
        except Exception as e:
            logger.warning(f"Sheets 쓰기 실패 (시도 {attempt}/3): {e}")
            if attempt < 3:
                time.sleep(10)

    if not write_ok:
        logger.error("Google Sheets 쓰기 3회 모두 실패")
        return

    # 셀 색상 적용
    # 이슈 가격: 빨간색, 품절: 짙은 회색, 기본(위너/할인 여부 무관): 검정
    issue_color   = {"red": 204 / 255, "green": 0.0,        "blue": 0.0}
    soldout_color = {"red": 153 / 255,  "green": 153 / 255,  "blue": 153 / 255}
    default_color = {"red": 0.0,        "green": 0.0,        "blue": 0.0}
    format_requests = []
    for r in results:
        if r.get("issue_price"):
            color = issue_color
        elif r.get("value") == "품절":
            color = soldout_color
        else:
            color = default_color
        format_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": ws.id,
                    "startRowIndex": r["row_index"] - 1,
                    "endRowIndex":   r["row_index"],
                    "startColumnIndex": col_idx - 1,
                    "endColumnIndex":   col_idx,
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"foregroundColor": color}
                    }
                },
                "fields": "userEnteredFormat.textFormat.foregroundColor",
            }
        })
    if format_requests:
        try:
            ws.spreadsheet.batch_update({"requests": format_requests})
            issue_count = sum(1 for r in results if r.get("issue_price"))
            logger.info(f"셀 색상 적용 완료 (이슈: {issue_count}개 / 기본: {len(format_requests) - issue_count}개)")
        except Exception as e:
            logger.warning(f"셀 색상 적용 실패: {e}")


# ============================================================
# SECTION 4: VIID → ItemId 매핑 (로컬 캐시)
# ============================================================

def load_viid_map() -> dict:
    if VIID_MAP_PATH.exists():
        with open(VIID_MAP_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_viid_map(viid_map: dict):
    with open(VIID_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(viid_map, f, ensure_ascii=False, indent=2)


# ============================================================
# SECTION 5: Chrome 브라우저 관리
# ============================================================

def _load_cookies(driver: uc.Chrome, cookie_file: str, logger: logging.Logger):
    """저장된 쿠키를 Chrome 세션에 주입합니다."""
    path = Path(cookie_file)
    if not path.exists():
        logger.warning(f"쿠키 파일 없음: {cookie_file}")
        return
    with open(path, encoding="utf-8") as f:
        cookies = json.load(f)
    loaded = 0
    for cookie in cookies:
        try:
            if "expiry" in cookie:
                cookie["expiry"] = int(cookie["expiry"])
            cookie.pop("sameSite", None)
            driver.add_cookie(cookie)
            loaded += 1
        except Exception:
            pass
    logger.info(f"쿠키 {loaded}/{len(cookies)}개 로드 완료")


def get_chrome_major_version() -> int | None:
    import winreg
    candidates = [
        (winreg.HKEY_CURRENT_USER, r"Software\Google\Chrome\BLBeacon"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Google\Chrome\BLBeacon"),
    ]
    for hive, subkey in candidates:
        try:
            with winreg.OpenKey(hive, subkey) as key:
                version, _ = winreg.QueryValueEx(key, "version")
                return int(version.split(".")[0])
        except Exception:
            continue
    return None


def create_browser(logger: logging.Logger, cookie_file: str = "", profile_dir: str = "") -> uc.Chrome:
    options = uc.ChromeOptions()

    # 전용 Chrome 프로필 사용 (회차 쌓일수록 실제 사용자처럼 보임)
    if profile_dir:
        Path(profile_dir).mkdir(parents=True, exist_ok=True)
        options.add_argument(f"--user-data-dir={profile_dir}")
        logger.info(f"Chrome 프로필 사용: {profile_dir}")

    # 창 크기 랜덤화 (고정 패턴 방지)
    win_width  = random.choice([600, 650, 700, 750, 800])
    win_height = random.choice([480, 500, 540, 580, 600])
    options.add_argument(f"--window-size={win_width},{win_height}")
    options.add_argument("--lang=ko-KR")

    chrome_ver = get_chrome_major_version()
    if chrome_ver:
        logger.info(f"Chrome 버전 감지: {chrome_ver}")
    else:
        logger.warning("Chrome 버전 자동 감지 실패 — undetected_chromedriver 기본값 사용")

    logger.info("Chrome 브라우저 시작 중...")
    driver = uc.Chrome(options=options, headless=False, **({} if chrome_ver is None else {"version_main": chrome_ver}))
    driver.set_window_size(win_width, win_height)
    driver.set_page_load_timeout(60)

    logger.info("쿠팡 홈페이지 접속 (쿠키 준비)...")
    driver.get("https://www.coupang.com/")
    time.sleep(3)

    if cookie_file:
        _load_cookies(driver, cookie_file, logger)
        try:
            driver.refresh()
            time.sleep(2)
        except Exception:
            logger.warning("쿠키 로드 후 새로고침 타임아웃 — 쿠키는 적용된 상태로 계속 진행합니다")

    return driver


def fetch_pid_html(driver: uc.Chrome, pid: str, delay: int) -> str:
    driver.get(f"https://www.coupang.com/vp/products/{pid}")
    # 기본 딜레이 + 랜덤 편차 (±3초)
    time.sleep(delay + random.uniform(-2, 4))
    return driver.page_source


def refresh_session(driver: uc.Chrome, logger: logging.Logger):
    """홈페이지 재방문으로 세션 리셋 (봇 감지 회피)"""
    logger.info("세션 리셋 중 (홈페이지 재방문)...")
    driver.get("https://www.coupang.com/")
    time.sleep(random.uniform(3, 5))



def fetch_viid_item_id(driver: uc.Chrome, pid: str, viid: str, delay: int, logger: logging.Logger) -> str:
    """loser VIID의 itemId를 알아내기 위해 해당 VIID URL로 추가 요청합니다.
    한 번 확인 후 viid_item_map에 캐시되므로 이후 실행에서는 호출되지 않습니다."""
    url = f"https://www.coupang.com/vp/products/{pid}?vendorItemId={viid}"
    logger.info(f"  VIID {viid} itemId 확인 중 (추가 요청)...")
    driver.get(url)
    time.sleep(delay + random.uniform(-1, 2))
    html = driver.page_source

    next_f = extract_next_f(html)
    patterns = [
        re.compile(rf'itemId=(\d+)[^"]*?vendorItemId={re.escape(viid)}'),
        re.compile(rf'itemId=(\d+)\\u0026[^"]*?vendorItemId={re.escape(viid)}'),
    ]
    for source in [next_f, html]:
        for pat in patterns:
            m = pat.search(source)
            if m:
                logger.info(f"  VIID {viid} → itemId {m.group(1)} 확인")
                return m.group(1)
    logger.warning(f"  VIID {viid}: itemId 추출 실패")
    return ""


def fetch_viid_price_info(driver: uc.Chrome, pid: str, viid: str, logger: logging.Logger) -> tuple[int | None, bool]:
    """위너 VIID 페이지에 직접 접속해 최종가와 할인 여부를 확인합니다.
    반환값: (price, is_discounted)"""
    url = f"https://www.coupang.com/vp/products/{pid}?vendorItemId={viid}"
    driver.get(url)
    time.sleep(random.uniform(3, 10))
    html = driver.page_source
    next_f = extract_next_f(html)
    price, is_discounted = _find_final_price_for_viid(next_f, viid)
    if price is None:
        price, is_discounted = _find_final_price_for_viid(html, viid)
    return price, is_discounted


def fetch_winner_seller(driver: uc.Chrome, pid: str, winner_viid: str, delay: int, logger: logging.Logger) -> tuple:
    """위너 VIID 페이지에 접속해 실제 판매자명과 활성 여부를 확인합니다.
    반환값: (seller: str, is_active: bool)
    결과는 호출부에서 캐시하므로 동일 winner_viid에 대해 1회만 호출됩니다."""
    logger.info(f"  위너 VIID {winner_viid} 판매자 확인 중 (추가 요청)...")
    url = f"https://www.coupang.com/vp/products/{pid}?vendorItemId={winner_viid}"
    driver.get(url)
    time.sleep(random.uniform(3, 10))
    html = driver.page_source
    seller = parse_winner_seller(html)
    is_active = winner_viid in parse_winners(html)
    if not is_active:
        logger.warning(f"  위너 VIID {winner_viid}: 해당 페이지에서 위너로 확인되지 않음 (판매중단 의심)")
    return seller, is_active


def fetch_my_viid_status(driver: uc.Chrome, pid: str, my_viid: str, delay: int, logger: logging.Logger) -> dict:
    """내 VIID 페이지에 직접 접속해 실제 위너 상태를 확인합니다.
    위너 VIID가 비활성(판매중단)으로 판정된 경우에만 호출됩니다.
    반환값: {"i_am_winner": bool, "price": int, "winner_seller": str, "winner_viid": str, "winner_price": int}
    """
    logger.info(f"  내 VIID {my_viid} 페이지 재확인 (비활성 위너 대체)...")
    url = f"https://www.coupang.com/vp/products/{pid}?vendorItemId={my_viid}"
    driver.get(url)
    time.sleep(random.uniform(3, 10))
    html = driver.page_source

    winner_map = parse_winners(html)
    seller = parse_winner_seller(html)

    if my_viid in winner_map:
        price = winner_map[my_viid]["price"]
        logger.info(f"  내 VIID {my_viid}: 실제 위너 확인 ✓ {price:,}원")
        return {"i_am_winner": True, "price": price, "winner_seller": "", "winner_viid": my_viid, "winner_price": price}

    winner_viid_real = next(iter(winner_map), "")
    winner_price_real = winner_map[winner_viid_real]["price"] if winner_viid_real else 0
    logger.info(f"  내 VIID {my_viid}: 비위너, 실제 위너 판매자={seller or '미확인'}")
    return {
        "i_am_winner": False,
        "price": 0,
        "winner_seller": seller,
        "winner_viid": winner_viid_real,
        "winner_price": winner_price_real,
    }


# ============================================================
# SECTION 6: 아이템위너 파싱
# ============================================================

def extract_next_f(html: str) -> str:
    """self.__next_f.push 청크를 하나의 문자열로 합칩니다."""
    pattern = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
    combined = ""
    for chunk in pattern.findall(html):
        try:
            combined += json.loads(f'"{chunk}"')
        except Exception:
            combined += chunk
    return combined


def _find_final_price_for_viid(next_f: str, viid: str) -> tuple[int | None, bool]:
    """VIID의 최종할인가(price 3)와 할인 여부를 반환합니다. (price, is_discounted)
    finalPrice.applicable=true인 경우만 최종할인가로 판단합니다."""
    for m in re.finditer(rf'"vendorItemId"\s*:\s*{re.escape(viid)}\b', next_f):
        window = next_f[m.start():m.start() + 700]
        fp = re.search(
            r'"finalPrice"\s*:\s*\{"applicable"\s*:\s*true[^}]*?"price"\s*:\s*(\d+)',
            window
        )
        if fp:
            return int(fp.group(1)), True
    return None, False


def parse_winners(html: str) -> dict:
    """
    페이지 HTML에서 아이템위너 VIID와 가격을 추출합니다.
    와우할인이 적용된 경우 최종 할인가를 사용합니다.

    반환값:
        {viid_str: {"price": int, "item_id": str}}
    """
    winner_map = {}

    next_f = extract_next_f(html)

    # addToCartUrl 패턴 (여러 인코딩 방식 대응)
    url_patterns = [
        # 디코딩 후 & 구분자
        re.compile(r'itemId=(\d+)&[^"]*?vendorItemId=(\d+)&[^"]*?price=(\d+)&[^"]*?isLoser=false'),
        # raw HTML & 구분자
        re.compile(r'itemId=(\d+)\\u0026[^"]*?vendorItemId=(\d+)\\u0026[^"]*?price=(\d+)\\u0026[^"]*?isLoser=false'),
        # 유연한 구분자 (100자 이내)
        re.compile(r'itemId=(\d+).{1,60}?vendorItemId=(\d+).{1,100}?price=(\d+).{1,60}?isLoser=false'),
    ]

    raw_winners = {}
    for source in [next_f, html]:
        if raw_winners:
            break
        for pat in url_patterns:
            for m in pat.finditer(source):
                viid = m.group(2)
                raw_winners[viid] = {
                    "price":   int(m.group(3)),
                    "item_id": m.group(1),
                }
            if raw_winners:
                break

    # 폴백: VIID만 추출 (가격/itemId 없음)
    if not raw_winners:
        for m in re.finditer(r'vendorItemId["\s\\]*:["\s\\]*(\d+)', html):
            winner_map[m.group(1)] = {"price": 0, "item_id": ""}
        return winner_map

    # 할인가로 교체 (WOW / 즉시할인 / discountRate 적용 여부 확인)
    for viid, info in raw_winners.items():
        final_price, is_discounted = _find_final_price_for_viid(next_f, viid)
        if final_price is None:
            final_price, is_discounted = _find_final_price_for_viid(html, viid)
        winner_map[viid] = {
            "price":     final_price if final_price is not None else info["price"],
            "item_id":   info["item_id"],
            "wow_price": is_discounted,
        }

    return winner_map


def parse_sold_out_viids(html: str) -> set:
    """soldOut=true인 VIID 집합을 반환합니다."""
    next_f = extract_next_f(html)
    sold_out = set()
    # soldOut:true 위치 기준으로 앞 300자 이내의 vendorItemId를 찾음
    # (두 필드 사이에 다른 필드가 끼어있는 구조 대응)
    for m in re.finditer(r'"soldOut"\s*:\s*true', next_f):
        context = next_f[max(0, m.start() - 300):m.start()]
        viid_m = re.search(r'"vendorItemId"\s*:\s*(\d+)', context)
        if viid_m:
            sold_out.add(viid_m.group(1))
    return sold_out


def parse_winner_seller(html: str) -> str:
    """현재 아이템위너의 판매자명을 반환합니다.
    쿠팡 직접 판매(로켓배송) 위너인 경우 '쿠팡'을 반환합니다."""
    next_f = extract_next_f(html)
    m = re.search(r'"sellerInfo"\s*:\s*\{"sellerName"\s*:\s*"([^"]+)"', next_f)
    if m:
        return m.group(1)
    if "PRODUCT_DETAIL_SELLER_INFO" in next_f:
        return "쿠팡"
    return ""


def parse_all_viid_items(html: str) -> dict:
    """위너/비위너 구분 없이 페이지의 모든 VIID→itemId 매핑을 추출합니다."""
    viid_to_item = {}
    next_f = extract_next_f(html)

    patterns = [
        re.compile(r'itemId=(\d+)&[^"]*?vendorItemId=(\d+)'),
        re.compile(r'itemId=(\d+)\\u0026[^"]*?vendorItemId=(\d+)'),
    ]

    for source in [next_f, html]:
        for pat in patterns:
            for m in pat.finditer(source):
                viid = m.group(2)
                if viid not in viid_to_item:
                    viid_to_item[viid] = m.group(1)
        if viid_to_item:
            break

    return viid_to_item


# ============================================================
# SECTION 7: Slack 알림
# ============================================================

def build_winner_url(a: dict) -> str:
    """현재 위너 직접 접속 URL 생성 (PID + 위너VIID만으로 충분)"""
    base = f"https://www.coupang.com/vp/products/{a['pid']}"
    if a.get("winner_viid"):
        return f"{base}?vendorItemId={a['winner_viid']}"
    return base


def send_slack_alert(webhook_url: str, alerts: list, logger: logging.Logger):
    if not alerts:
        return
    if not webhook_url or "여기에" in webhook_url:
        logger.warning("Slack 웹훅 URL이 설정되지 않아 알림을 건너뜁니다.")
        return

    today_str = date.today().strftime("%Y-%m-%d")

    # PID별 그룹핑
    pid_groups: dict = {}
    for a in alerts:
        pid_groups.setdefault(a["pid"], []).append(a)

    pid_list = " ".join(pid_groups.keys())
    lines = [
        f"[아이템위너 이슈 알림] {today_str} 기준 / {len(alerts)}개",
        f"[PID] {pid_list}\n",
    ]
    for pid, group in pid_groups.items():
        sku_name = group[0]["sku_name"]
        lines.append(f"▪ {sku_name} (PID: {pid})")
        for a in group:
            price_str = f"{a['winner_price']:,}원" if a["winner_price"] else "가격 미확인"
            seller_str = a.get("winner_seller", "미확인")
            lines.append(f"  {seller_str} | {price_str} | {build_winner_url(a)}")
        lines.append("")

    payload = {"message": "\n".join(lines)}
    try:
        resp = requests.post(webhook_url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info(f"Slack 알림 발송 완료 ({len(alerts)}건)")
    except Exception as e:
        logger.error(f"Slack 알림 실패: {e}")


def copy_log_to_nas(log_file: Path, nas_path: str, logger: logging.Logger):
    """실행 완료 후 오늘 로그 파일을 NAS에 복사합니다."""
    if not nas_path:
        return
    try:
        nas_dir = Path(nas_path)
        nas_dir.mkdir(parents=True, exist_ok=True)
        dest = nas_dir / log_file.name
        shutil.copy2(log_file, dest)
        logger.info(f"NAS 로그 업로드 완료: {dest}")
    except Exception as e:
        logger.warning(f"NAS 로그 업로드 실패 (네트워크 연결 확인): {e}")


def save_issue_log(alerts: list, logger: logging.Logger):
    """이슈 발생 이력을 CSV 파일에 누적 저장"""
    if not alerts:
        return

    fieldnames = ["날짜", "시간", "브랜드명", "SKU명", "내VIID", "현재위너VIID", "현재위너가격(원)", "직접접속URL", "URL(텍스트)"]
    file_exists = ISSUE_LOG_PATH.exists()

    try:
        with open(ISSUE_LOG_PATH, "a", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            if not file_exists:
                writer.writeheader()

            now = datetime.now()
            for a in alerts:
                writer.writerow({
                    "날짜":           now.strftime("%Y-%m-%d"),
                    "시간":           now.strftime("%H:%M:%S"),
                    "브랜드명":       a.get("brand", ""),
                    "SKU명":          a["sku_name"],
                    "내VIID":         a["lost_viid"],
                    "현재위너VIID":   a.get("winner_viid", "미확인"),
                    "현재위너가격(원)": a["winner_price"],
                    "직접접속URL":    f'=HYPERLINK("{build_winner_url(a)}","{build_winner_url(a)}")',
                    "URL(텍스트)":   build_winner_url(a),
                })
        logger.info(f"이슈로그.csv 기록 완료 ({len(alerts)}건) → {ISSUE_LOG_PATH}")
    except PermissionError:
        logger.warning(f"이슈로그.csv 쓰기 실패 — 파일이 다른 프로그램(Excel 등)에서 열려 있습니다. 닫은 후 재시도하세요.")


# ============================================================
# SECTION 8: Main Execution
# ============================================================

def main():
    config = load_config()
    logger = setup_logger(config["log_dir"])
    logger.info("===== 쿠팡 아이템위너 모니터링 시작 =====")

    # VIID → itemId 로컬 매핑 로드
    viid_item_map = load_viid_map()

    # Google Sheets 연결
    try:
        ws = get_worksheet(config)
        logger.info("Google Sheets 연결 성공")
    except Exception as e:
        logger.error(f"Google Sheets 연결 실패: {e}")
        sys.exit(1)

    rows, headers = load_sheet_rows(ws)
    if not rows:
        logger.warning("시트에 데이터 행이 없습니다.")
        sys.exit(0)
    logger.info(f"시트에서 {len(rows)}개 행 로드")

    try:
        today_col_idx = get_or_create_date_column(ws, headers)
    except Exception as e:
        logger.error(f"날짜 컬럼 생성 실패: {e}")
        sys.exit(1)
    logger.info(f"오늘 날짜 컬럼: {col_index_to_letter(today_col_idx)} ({date.today().strftime('%Y-%m-%d')})")

    # PID별 그룹핑
    pid_groups: dict = defaultdict(list)
    for row in rows:
        pid_groups[row["pid"]].append(row)

    # 커맨드라인 인수로 특정 PID만 지정 가능: python main.py 12345 67890
    target_pids = [a for a in sys.argv[1:] if a.isdigit()]
    if target_pids:
        pid_groups = {p: pid_groups[p] for p in target_pids if p in pid_groups}
        unknown = [p for p in target_pids if p not in pid_groups]
        if unknown:
            logger.warning(f"시트에 없는 PID 무시: {unknown}")
        logger.info(f"지정 PID {len(pid_groups)}개만 처리: {list(pid_groups.keys())}")
    else:
        logger.info(f"고유 PID {len(pid_groups)}개 처리 예정")

    # 재실행 시 이미 처리된 PID 건너뜀 (오늘 컬럼에 값이 있는 행 확인)
    try:
        today_col_values = ws.col_values(today_col_idx)
        done_row_indices = {
            row["row_index"]
            for row in rows
            if row["row_index"] <= len(today_col_values)
            and today_col_values[row["row_index"] - 1].strip()
        }
    except Exception as e:
        logger.warning(f"기존 처리 현황 로드 실패 (전체 재처리): {e}")
        done_row_indices = set()

    all_pids_done = all(
        all(r["row_index"] in done_row_indices for r in pid_rows)
        for pid_rows in pid_groups.values()
    )

    if all_pids_done:
        # 이전 실행이 정상 완료된 경우 → 새 회차 실행 (덮어쓰기)
        logger.info("이전 실행 완료 확인 → 새 회차 실행 (전체 재처리)")
    elif done_row_indices:
        # 일부만 처리된 경우 → 중간에 끊긴 것 → 이어서 실행
        skip_pids = [pid for pid, pid_rows in pid_groups.items()
                     if all(r["row_index"] in done_row_indices for r in pid_rows)]
        for pid in skip_pids:
            del pid_groups[pid]
        logger.info(f"재실행 감지: {len(skip_pids)}개 PID 건너뜀 → {len(pid_groups)}개 PID 처리 예정")

    # Chrome 브라우저 1회 시작
    # chrome_profile은 항상 로컬 PC에 저장 (NAS 실행 시 네트워크 드라이브에 생기는 것 방지)
    default_profile = str(Path.home() / "AppData" / "Local" / "CoupangMonitor" / "chrome_profile")
    profile_dir = config.get("chrome_profile_dir") or default_profile
    driver = create_browser(logger, config.get("cookie_file", ""), profile_dir=profile_dir)

    results = []
    alerts = []
    viid_map_updated = False
    winner_seller_cache: dict = {}  # winner_viid → (판매자명, is_active) 캐시

    try:
        for pid_count, (pid, pid_rows) in enumerate(pid_groups.items()):
            # 2~5개 PID마다 세션 리셋 (고정 패턴 방지)
            if pid_count > 0 and pid_count % random.randint(2, 5) == 0:
                refresh_session(driver, logger)

            logger.info(f"PID {pid} 처리 중 ({len(pid_rows)}개 VIID)...")
            pid_result_start = len(results)
            pid_alerts_start = len(alerts)
            for attempt in range(1, 3):  # 최대 2회 시도
                try:
                    html = fetch_pid_html(driver, pid, config["request_delay_seconds"])

                    if len(html) < 1000:
                        preview = html[:200].replace("\n", " ")
                        logger.warning(f"  차단 응답 미리보기: {preview}")
                        logger.warning(f"PID {pid}: 봇 차단 감지 — 60초 대기 후 자동 재시도...")
                        time.sleep(60)
                        refresh_session(driver, logger)
                        html = fetch_pid_html(driver, pid, config["request_delay_seconds"])
                        if len(html) < 1000:
                            raise ValueError(f"페이지 크기 비정상: {len(html)} bytes (봇 차단 의심, 재시도 후)")

                    # next_f 크기가 너무 작으면 봇 차단 페이지로 판단
                    next_f_preview = extract_next_f(html)
                    if len(next_f_preview) < 5000:
                        title_m = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
                        title = title_m.group(1).strip() if title_m else "미확인"
                        logger.warning(
                            f"  봇 차단 의심 — HTML {len(html):,}bytes / next_f {len(next_f_preview)}chars / 타이틀: {title}"
                        )
                        logger.warning(f"PID {pid}: 봇 차단 감지 — 60초 대기 후 자동 재시도...")
                        time.sleep(60)
                        refresh_session(driver, logger)
                        html = fetch_pid_html(driver, pid, config["request_delay_seconds"])
                        next_f_preview = extract_next_f(html)
                        if len(next_f_preview) < 5000:
                            raise ValueError(f"next_f 데이터 부족 ({len(next_f_preview)}chars) — 봇 차단 의심 (재시도 후)")

                    winner_map = parse_winners(html)

                    # 파싱 실패 시 홈 재방문 후 1회 재시도
                    if not winner_map:
                        logger.warning(f"PID {pid}: 위너 추출 실패, 세션 리셋 후 재시도...")
                        refresh_session(driver, logger)
                        html = fetch_pid_html(driver, pid, config["request_delay_seconds"])
                        winner_map = parse_winners(html)

                    if not winner_map:
                        raise ValueError("위너 VIID를 추출하지 못했습니다 (재시도 후)")

                    logger.info(f"PID {pid}: 위너 VIID {len(winner_map)}개 확인")

                    # 페이지의 전체 VIID→itemId 매핑 갱신 (비위너 VIID 포함)
                    full_viid_map = parse_all_viid_items(html)
                    for viid, iid in full_viid_map.items():
                        if viid not in viid_item_map and iid:
                            viid_item_map[viid] = iid
                            viid_map_updated = True

                    sold_out_viids = parse_sold_out_viids(html)

                    for row in pid_rows:
                        my_viid = row["viid"]

                        if my_viid in sold_out_viids:
                            results.append({"row_index": row["row_index"], "value": "품절"})
                            logger.info(f"  {row['sku_name']} VIID {my_viid}: 품절")
                        elif my_viid in winner_map:
                            price   = winner_map[my_viid]["price"]
                            item_id = winner_map[my_viid]["item_id"]
                            is_wow  = winner_map[my_viid].get("wow_price", False)

                            # itemId 매핑 갱신
                            if item_id and viid_item_map.get(my_viid) != item_id:
                                viid_item_map[my_viid] = item_id
                                viid_map_updated = True

                            # 할인 여부 미확인 시 해당 VIID 페이지에서 재확인
                            if not is_wow:
                                fetched_price, is_discounted = fetch_viid_price_info(driver, pid, my_viid, logger)
                                if fetched_price is not None:
                                    price  = fetched_price
                                    is_wow = is_discounted
                                    logger.info(f"  {row['sku_name']} VIID {my_viid}: 할인가 재확인 {price:,}원")

                            results.append({"row_index": row["row_index"], "value": price, "wow_price": is_wow})
                            logger.info(f"  {row['sku_name']} VIID {my_viid}: 위너 ✓ {price:,}원{'  (할인)' if is_wow else ''}")
                        else:
                            # 비위너 처리
                            # itemId로 내 옵션의 위너를 특정
                            my_viid_set = {r["viid"] for r in pid_rows}
                            item_id = viid_item_map.get(my_viid, "")
                            if not item_id:
                                item_id = fetch_viid_item_id(
                                    driver, pid, my_viid,
                                    config["request_delay_seconds"], logger
                                )
                                if item_id:
                                    viid_item_map[my_viid] = item_id
                                    viid_map_updated = True

                            # 같은 itemId의 위너 찾기
                            matched_winner_viid  = ""
                            matched_winner_price = 0
                            if item_id:
                                for w_viid, w_info in winner_map.items():
                                    if w_info["item_id"] == item_id:
                                        matched_winner_viid  = w_viid
                                        matched_winner_price = w_info["price"]
                                        break

                            if not matched_winner_viid:
                                # 같은 itemId 위너 없음 → 미노출(개당가 불리) 옵션
                                results.append({"row_index": row["row_index"], "value": "-"})
                                logger.info(
                                    f"  {row['sku_name']} VIID {my_viid}: 비위너이나 이슈 제외 "
                                    f"(미노출 — 동일 옵션 위너 없음)"
                                )
                            elif matched_winner_viid in my_viid_set:
                                # 같은 옵션 위너가 내 다른 VIID
                                results.append({"row_index": row["row_index"], "value": "-"})
                                logger.info(
                                    f"  {row['sku_name']} VIID {my_viid}: 비위너이나 이슈 제외 "
                                    f"(위너가 내 다른 VIID {matched_winner_viid})"
                                )
                            else:
                                # 위너 VIID 판매자 + 활성 여부 확인 (캐시 활용)
                                allowed = config.get("allowed_winner_sellers", [])

                                if matched_winner_viid in winner_seller_cache:
                                    seller, is_active = winner_seller_cache[matched_winner_viid]
                                else:
                                    seller, is_active = fetch_winner_seller(
                                        driver, pid, matched_winner_viid,
                                        config["request_delay_seconds"], logger
                                    )
                                    winner_seller_cache[matched_winner_viid] = (seller, is_active)

                                if is_active:
                                    if seller in allowed:
                                        results.append({"row_index": row["row_index"], "value": "-"})
                                        logger.info(
                                            f"  {row['sku_name']} VIID {my_viid}: 비위너이나 이슈 제외 "
                                            f"(위너 판매자 허용: {seller})"
                                        )
                                    else:
                                        results.append({"row_index": row["row_index"], "value": matched_winner_price, "issue_price": True})
                                        alerts.append({
                                            "brand":         row["brand"],
                                            "sku_name":      row["sku_name"],
                                            "lost_viid":     my_viid,
                                            "winner_viid":   matched_winner_viid,
                                            "winner_price":  matched_winner_price,
                                            "winner_seller": seller,
                                            "item_id":       item_id,
                                            "pid":           pid,
                                        })
                                        logger.warning(
                                            f"  {row['sku_name']} VIID {my_viid}: 이슈 ✗ "
                                            f"위너 판매자={seller} 가격={matched_winner_price:,}원"
                                        )
                                else:
                                    # 위너 VIID 비활성(판매중단) → 내 VIID 페이지 재확인
                                    status = fetch_my_viid_status(
                                        driver, pid, my_viid,
                                        config["request_delay_seconds"], logger
                                    )
                                    if status["i_am_winner"]:
                                        price = status["price"]
                                        results.append({"row_index": row["row_index"], "value": price})
                                        logger.info(
                                            f"  {row['sku_name']} VIID {my_viid}: 위너 확인 (비활성 위너 대체) ✓ {price:,}원"
                                        )
                                    else:
                                        real_seller      = status["winner_seller"]
                                        real_winner_viid = status["winner_viid"]
                                        real_winner_price = status["winner_price"]

                                        if not real_winner_viid:
                                            results.append({"row_index": row["row_index"], "value": "-"})
                                            logger.info(
                                                f"  {row['sku_name']} VIID {my_viid}: 비위너이나 이슈 제외 (위너 없음)"
                                            )
                                        elif real_winner_viid in my_viid_set:
                                            results.append({"row_index": row["row_index"], "value": "-"})
                                            logger.info(
                                                f"  {row['sku_name']} VIID {my_viid}: 비위너이나 이슈 제외 "
                                                f"(위너가 내 다른 VIID {real_winner_viid})"
                                            )
                                        elif real_seller in allowed:
                                            results.append({"row_index": row["row_index"], "value": "-"})
                                            logger.info(
                                                f"  {row['sku_name']} VIID {my_viid}: 비위너이나 이슈 제외 "
                                                f"(위너 판매자 허용: {real_seller})"
                                            )
                                        else:
                                            results.append({"row_index": row["row_index"], "value": real_winner_price, "issue_price": True})
                                            alerts.append({
                                                "brand":         row["brand"],
                                                "sku_name":      row["sku_name"],
                                                "lost_viid":     my_viid,
                                                "winner_viid":   real_winner_viid,
                                                "winner_price":  real_winner_price,
                                                "winner_seller": real_seller,
                                                "item_id":       item_id,
                                                "pid":           pid,
                                            })
                                            logger.warning(
                                                f"  {row['sku_name']} VIID {my_viid}: 이슈 ✗ "
                                                f"위너 판매자={real_seller} 가격={real_winner_price:,}원"
                                            )

                    break  # 정상 처리 완료 → 재시도 루프 탈출

                except ValueError as e:
                    logger.error(f"PID {pid}: {e} — 건너뜀")
                    del results[pid_result_start:]
                    del alerts[pid_alerts_start:]
                    for row in pid_rows:
                        results.append({"row_index": row["row_index"], "value": "오류(파싱)"})
                    break  # 파싱 오류는 재시도 불필요

                except Exception as e:
                    err_msg = str(e)
                    # 브라우저가 죽었을 때 발생하는 오류 패턴 (봇 차단 후 Chrome 종료 포함)
                    BROWSER_DEAD = [
                        "HTTPConnectionPool", "Read timed out",
                        "invalid session id", "no such window",
                        "web view not found", "session deleted",
                        "disconnected: not connected",
                    ]
                    is_conn_err = any(kw in err_msg for kw in BROWSER_DEAD)

                    if is_conn_err and attempt == 1:
                        logger.warning(f"PID {pid}: 브라우저 종료 감지 — 재시작 후 재시도...")
                        del results[pid_result_start:]
                        del alerts[pid_alerts_start:]
                        try:
                            driver.quit()
                        except Exception:
                            pass
                        driver = create_browser(logger, config.get("cookie_file", ""), profile_dir=profile_dir)
                        # attempt 2로 자동 진행
                    else:
                        logger.error(f"PID {pid}: 예상치 못한 오류 — {err_msg[:120]} — 건너뜀")
                        del results[pid_result_start:]
                        del alerts[pid_alerts_start:]
                        for row in pid_rows:
                            results.append({"row_index": row["row_index"], "value": "오류"})
                        break

            # 안전망: 어떤 이유로든 결과 미기록 행은 "오류"로 채움
            recorded = {r["row_index"] for r in results}
            for row in pid_rows:
                if row["row_index"] not in recorded:
                    logger.warning(f"  {row['sku_name']}: 결과 누락 — 오류로 기록")
                    results.append({"row_index": row["row_index"], "value": "오류"})

            # PID 처리 완료 즉시 Google Sheets에 기록
            pid_results = results[pid_result_start:]
            pid_all_errors = pid_results and all(str(r["value"]).startswith("오류") for r in pid_results)
            if pid_all_errors:
                logger.warning(f"PID {pid}: 전체 오류 — Google Sheets 업데이트 건너뜀 (이전 데이터 유지)")
            else:
                write_results_batch(ws, pid_results, today_col_idx, logger)

    finally:
        try:
            driver.quit()
        except Exception:
            pass

    # VIID 매핑 저장
    if viid_map_updated:
        save_viid_map(viid_item_map)
        logger.info("viid_map.json 갱신 완료")

    # 이슈 로그 CSV 저장 + Slack 알림
    if alerts:
        save_issue_log(alerts, logger)
        send_slack_alert(config["slack_webhook_url"], alerts, logger)
    else:
        logger.info("이슈 없음 — Slack 알림 불필요")

    logger.info(f"===== 완료: 총 {len(rows)}개 VIID, 이슈 {len(alerts)}건 =====")

    # NAS 업로드 (로그 + 이슈로그 CSV)
    nas_log_path = config.get("nas_log_path", "")
    if nas_log_path:
        log_file = Path(config["log_dir"]) / f"{date.today().strftime('%Y-%m-%d')}.log"
        copy_log_to_nas(log_file, nas_log_path, logger)
        if ISSUE_LOG_PATH.exists():
            copy_log_to_nas(ISSUE_LOG_PATH, nas_log_path, logger)


if __name__ == "__main__":
    main()
