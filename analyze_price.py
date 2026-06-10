import re, json
import sys

# HTML 파일 읽기
filepath = sys.argv[1] if len(sys.argv) > 1 else "page_raw_5562880089.html"

with open(filepath, 'r', encoding='utf-8') as f:
    html = f.read()

# __next_f 청크 추출
pattern = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\]|\.)*)"\]\)')
combined = ''
chunks_count = 0
for chunk in pattern.findall(html):
    try:
        combined += json.loads(f'"{chunk}"')
        chunks_count += 1
    except Exception as e:
        combined += chunk

print(f'=== __next_f 데이터 분석 ===')
print(f'청크 개수: {chunks_count}')
print(f'합산 크기: {len(combined):,} chars')
print()

# vendorItemId 위치별로 근처 가격 필드 추출
print(f'=== 가격 필드 구조 분석 ===')
found_samples = {}

for m in re.finditer(r'"vendorItemId"\s*:\s*(\d+)', combined):
    viid = m.group(1)
    
    # vendorItemId 위치에서 600자 윈도우
    start = m.start()
    end = min(len(combined), m.start() + 800)
    window = combined[start:end]
    
    # 중복 방지
    if viid in found_samples:
        continue
    
    # 각 필드 찾기
    sample = {
        'viid': viid,
        'fields': {}
    }
    
    # finalPrice
    fp = re.search(r'"finalPrice"\s*:\s*(\{[^}]+\})', window)
    if fp:
        sample['fields']['finalPrice'] = fp.group(1)
    
    # originalPrice
    op = re.search(r'"originalPrice"\s*:\s*(\d+)', window)
    if op:
        sample['fields']['originalPrice'] = op.group(1)
    
    # salePrice
    sp = re.search(r'"salePrice"\s*:\s*(\d+)', window)
    if sp:
        sample['fields']['salePrice'] = sp.group(1)
    
    # wowMemberPrice
    wmp = re.search(r'"wowMemberPrice"\s*:\s*(\{[^}]+\})', window)
    if wmp:
        sample['fields']['wowMemberPrice'] = wmp.group(1)
    
    # discountRate
    dr = re.search(r'"discountRate"\s*:\s*(\d+)', window)
    if dr:
        sample['fields']['discountRate'] = dr.group(1)
    
    # coupon (object)
    coupon = re.search(r'"coupon"\s*:\s*(\{[^}]+\})', window)
    if coupon:
        sample['fields']['coupon'] = coupon.group(1)
    
    # isLoser
    isloser = re.search(r'"isLoser"\s*:\s*(true|false)', window)
    if isloser:
        sample['fields']['isLoser'] = isloser.group(1)
    
    # soldOut
    sold = re.search(r'"soldOut"\s*:\s*(true|false)', window)
    if sold:
        sample['fields']['soldOut'] = sold.group(1)
    
    if sample['fields']:
        found_samples[viid] = sample

print(f'분석된 VIID: {len(found_samples)}개')
print()

# 샘플 출력 (최대 3개)
for i, (viid, sample) in enumerate(list(found_samples.items())[:3], 1):
    print(f'[샘플 {i}] VIID: {viid}')
    for field, value in sample['fields'].items():
        value_preview = value[:80] if len(value) > 80 else value
        print(f'  {field}: {value_preview}')
    print()

# 필드별 통계
field_stats = {}
for sample in found_samples.values():
    for field in sample['fields']:
        field_stats[field] = field_stats.get(field, 0) + 1

print('=== 필드별 발현율 ===')
for field, count in sorted(field_stats.items(), key=lambda x: -x[1]):
    pct = (count / len(found_samples)) * 100 if found_samples else 0
    print(f'{field}: {count}/{len(found_samples)} ({pct:.1f}%)')
