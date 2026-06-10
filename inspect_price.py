"""
옵션별 wowMemberPrice 포함 여부 확인용 임시 스크립트
"""
import re, json, time, random
import undetected_chromedriver as uc

PID   = "8405123971"
VIIDs = ["91314681147", "91319871515", "91319871626", "92919100575"]

def extract_next_f(html):
    pattern = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
    combined = ""
    for chunk in pattern.findall(html):
        try:
            combined += json.loads(f'"{chunk}"')
        except Exception:
            combined += chunk
    return combined

options = uc.ChromeOptions()
options.add_argument("--window-size=1920,1080")
driver = uc.Chrome(options=options, headless=False, version_main=147)
driver.set_page_load_timeout(60)

try:
    driver.get("https://www.coupang.com/")
    time.sleep(3)
    driver.get(f"https://www.coupang.com/vp/products/{PID}")
    time.sleep(8 + random.uniform(0, 2))

    next_f = extract_next_f(driver.page_source)

    for viid in VIIDs:
        print(f"\n{'='*60}")
        print(f"VIID {viid}")
        for m in re.finditer(rf'"vendorItemId"\s*:\s*{viid}\b', next_f):
            window = next_f[m.start():m.start() + 1000]
            wow = re.search(r'"wowMemberPrice"\s*:\s*\{"price"\s*:\s*(\d+)', window)
            final = re.search(r'"finalPrice"\s*:\s*\{"applicable"\s*:\s*true[^}]*?"price"\s*:\s*(\d+)', window)
            if wow or final:
                print(f"  wowMemberPrice : {wow.group(1) if wow else '없음'}")
                print(f"  finalPrice     : {final.group(1) if final else '없음'}")
                break
        else:
            print("  vendorItemId 근처에서 가격 정보 없음")
finally:
    driver.quit()
    print("\n완료")
