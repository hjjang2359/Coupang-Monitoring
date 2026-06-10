"""
쿠팡 상품 페이지 소스 구조 확인 유틸리티

실행 방법:
    python inspect_page.py
    또는
    python inspect_page.py 12345678  (PID 직접 입력)

출력:
    - 콘솔: vendorItemId / itemId 관련 패턴 탐색 결과
    - 파일: page_raw_{PID}.html (전체 HTML 저장)
"""

import sys
import re
import json
import time
from pathlib import Path
import undetected_chromedriver as uc


def fetch_html(pid: str) -> str:
    url = f"https://www.coupang.com/vp/products/{pid}"
    print(f"\n[요청] {url}")
    print("[준비] 브라우저 실행 중...")

    options = uc.ChromeOptions()
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--lang=ko-KR")

    driver = uc.Chrome(options=options, headless=False, version_main=147)
    try:
        print("[준비] 쿠팡 홈페이지 접속 중...")
        driver.get("https://www.coupang.com/")
        time.sleep(3)

        print("[요청] 상품 페이지 접속 중...")
        driver.get(url)
        time.sleep(4)

        html = driver.page_source
    finally:
        driver.quit()

    print(f"[응답] 페이지 크기: {len(html):,} bytes")
    return html


def extract_next_f_data(html: str) -> str:
    """self.__next_f.push 청크들을 하나의 문자열로 합칩니다."""
    pattern = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
    chunks = pattern.findall(html)
    combined = ""
    for chunk in chunks:
        try:
            combined += json.loads(f'"{chunk}"')
        except Exception:
            combined += chunk
    return combined


def search_vendor_items(text: str, label: str):
    """vendorItemId 패턴 탐색 및 출력"""
    # 다양한 패턴으로 검색
    patterns = [
        (r'"vendorItemId"\s*:\s*(\d+)', "vendorItemId (숫자)"),
        (r'"vendorItemId"\s*:\s*"(\d+)"', "vendorItemId (문자열)"),
        (r'vendorItemId\\?"\\?:\s*\\?"?(\d+)', "vendorItemId (이스케이프)"),
    ]

    found = {}
    for pattern, desc in patterns:
        matches = re.findall(pattern, text)
        for m in matches:
            found[m] = desc

    if found:
        print(f"\n  [{label}] vendorItemId 발견: {len(found)}개")
        for viid, desc in list(found.items())[:20]:
            print(f"    VIID: {viid}  ({desc})")
    else:
        print(f"\n  [{label}] vendorItemId 미발견")

    return list(found.keys())


def search_item_ids(text: str, label: str):
    """itemId 패턴 탐색 및 출력"""
    patterns = [
        r'"itemId"\s*:\s*(\d+)',
        r'"itemId"\s*:\s*"(\d+)"',
    ]
    found = set()
    for pattern in patterns:
        for m in re.findall(pattern, text):
            found.add(m)

    if found:
        print(f"  [{label}] itemId 발견: {len(found)}개")
        for iid in list(found)[:10]:
            print(f"    ItemId: {iid}")
    return list(found)


def show_context(html: str, viid: str):
    """특정 VIID 주변 컨텍스트 출력"""
    pattern = re.compile(rf'vendorItemId[^0-9]*{viid}')
    for i, match in enumerate(pattern.finditer(html)):
        if i >= 2:
            break
        start = max(0, match.start() - 150)
        end = min(len(html), match.end() + 150)
        ctx = html[start:end].replace("\n", " ").replace("  ", " ")
        print(f"\n  컨텍스트 [{i+1}]: ...{ctx}...")


def main():
    if len(sys.argv) > 1:
        pid = sys.argv[1].strip()
    else:
        pid = input("확인할 PID를 입력하세요: ").strip()

    if not pid.isdigit():
        print(f"[오류] PID는 숫자여야 합니다. 입력값: {pid!r}")
        sys.exit(1)

    html = fetch_html(pid)

    # HTML 저장
    out_path = Path(f"page_raw_{pid}.html")
    out_path.write_text(html, encoding="utf-8")
    print(f"[저장] 전체 HTML → {out_path.resolve()}")

    print(f"\n{'='*60}")
    print(f"  PID {pid} — 탐색 결과")
    print(f"{'='*60}")

    # 1. raw HTML에서 직접 탐색
    viids_raw = search_vendor_items(html, "raw HTML")
    search_item_ids(html, "raw HTML")

    # 2. __next_f 청크 데이터에서 탐색
    print("\n  __next_f 청크 데이터 파싱 중...")
    next_f = extract_next_f_data(html)
    if next_f:
        print(f"  청크 합산 크기: {len(next_f):,} chars")
        viids_nf = search_vendor_items(next_f, "__next_f")
        search_item_ids(next_f, "__next_f")
    else:
        print("  __next_f 청크 미발견")
        viids_nf = []

    # 3. 발견된 VIID 컨텍스트 확인
    all_viids = list(dict.fromkeys(viids_raw + viids_nf))
    if all_viids:
        print(f"\n{'='*60}")
        print(f"  발견된 VIID 목록: {all_viids[:20]}")
        print(f"{'='*60}")
        print("\n  첫 번째 VIID 주변 컨텍스트:")
        show_context(html, all_viids[0])
    else:
        print("\n  vendorItemId를 찾지 못했습니다.")
        print(f"  page_raw_{pid}.html 파일을 브라우저로 열어 직접 확인하세요.")

    print(f"\n{'='*60}")
    print("  결과를 클로드에 붙여넣으면 main.py 파싱 로직을 완성해드립니다.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
