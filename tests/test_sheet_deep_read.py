"""Deep structural analysis of the sample sheet"""
import requests, json, os
from dotenv import load_dotenv
load_dotenv()
UAT = os.getenv('FEISHU_UAT')
headers = {'Authorization': f'Bearer {UAT}'}
SHEET_TOKEN = 'LC1BsAqWph5DxstTwXYcojsjnic'

def num_to_col(n):
    result = ''
    n += 1
    while n > 0:
        n -= 1
        result = chr(ord('A') + n % 26) + result
        n //= 26
    return result

# 1. All tabs metadata
url = f'https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{SHEET_TOKEN}/sheets/query'
resp = requests.get(url, headers=headers, timeout=15)
sheets = resp.json().get('data',{}).get('sheets',[])

print("ALL TABS:")
for s in sheets:
    grid = s.get('grid_properties', {})
    merges = s.get('merges', [])
    print(f"  {s.get('title','?')} (id={s.get('sheet_id')}) "
          f"rows={grid.get('row_count','?')} cols={grid.get('column_count','?')} "
          f"hidden={s.get('hidden',False)} merges={len(merges)}")

# 2. Deep read: 营销方案申请表单
target_id = '8Je1do'
print(f"\n{'='*70}")
print(f"  Deep read: 营销方案申请表单 ({target_id})")
print(f"{'='*70}")

for s in sheets:
    if s.get('sheet_id') == target_id:
        merges = s.get('merges', [])
        print(f"\n  Merges ({len(merges)}):")
        for m in merges:
            sr, er = m['start_row_index'], m['end_row_index']
            sc, ec = m['start_column_index'], m['end_column_index']
            span_r = er - sr + 1
            span_c = ec - sc + 1
            print(f"    R{sr+1}-R{er+1} × C{num_to_col(sc)}-C{num_to_col(ec)}  ({span_r}rows × {span_c}cols)")
        break

# Read first 35 rows
range_str = f"{target_id}!A1:{num_to_col(21)}35"
url2 = (f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets"
        f"/{SHEET_TOKEN}/values/{range_str}?valueRenderOption=FormattedValue")
resp2 = requests.get(url2, headers=headers, timeout=15)
values = resp2.json().get('data',{}).get('valueRange',{}).get('values',[])

print(f"\n  Data rows: {len(values)}\n")
print(f"  {'ROW':>5} [fill/tot]  A | B | C | D | E | F")
print(f"  {'-'*70}")
for i, row in enumerate(values):
    cells = []
    for j in range(min(len(row) if row else 0, 15)):
        cell = row[j] if row and j < len(row) else None
        if cell is None:
            cells.append('·')
        else:
            s = str(cell).replace('\n','\\n').strip()
            cells.append(s[:30] if s else '·')
    while len(cells) < 6:
        cells.append('·')
    non_empty = sum(1 for c in (row or []) if c is not None and str(c).strip())
    total_cols = len(row) if row else 0
    print(f"  R{i+1:3d}  [{non_empty:2d}/{total_cols:2d}]  {' | '.join(cells[:6])}")

# 3. Also read another tab to see variety — "工作汇总" (12e766)
target2_id = '12e766'
print(f"\n{'='*70}")
print(f"  Deep read: 工作汇总 ({target2_id})")
print(f"{'='*70}")

for s in sheets:
    if s.get('sheet_id') == target2_id:
        merges = s.get('merges', [])
        print(f"\n  Merges ({len(merges)}):")
        for m in merges[:15]:
            sr, er = m['start_row_index'], m['end_row_index']
            sc, ec = m['start_column_index'], m['end_column_index']
            print(f"    R{sr+1}-R{er+1} × C{num_to_col(sc)}-C{num_to_col(ec)}")
        if len(merges) > 15:
            print(f"    ... and {len(merges)-15} more")
        break

range_str2 = f"{target2_id}!A1:{num_to_col(17)}30"
url3 = (f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets"
        f"/{SHEET_TOKEN}/values/{range_str2}?valueRenderOption=FormattedValue")
resp3 = requests.get(url3, headers=headers, timeout=15)
values2 = resp3.json().get('data',{}).get('valueRange',{}).get('values',[])

print(f"\n  Data rows: {len(values2)}\n")
for i, row in enumerate(values2[:25]):
    cells = []
    for j in range(min(len(row) if row else 0, 8)):
        cell = row[j] if row and j < len(row) else None
        if cell is None:
            cells.append('·')
        else:
            s = str(cell).replace('\n','\\n').strip()
            cells.append(s[:25] if s else '·')
    while len(cells) < 6:
        cells.append('·')
    non_empty = sum(1 for c in (row or []) if c is not None and str(c).strip())
    total_cols = len(row) if row else 0
    print(f"  R{i+1:3d}  [{non_empty:2d}/{total_cols:2d}]  {' | '.join(cells[:8])}")

# 4. Read the bitable with "contains" filter test
print(f"\n{'='*70}")
print(f"  Bitable: test 'contains' filter")
print(f"{'='*70}")

BITABLE_TOKEN = "S4swbS3G9a74nmsYeKJcjIo6nnh"
TABLE_ID = "tbl7hZD9CLvShHWr"
_BASE = "https://open.feishu.cn/open-apis/bitable/v1"

bt_headers = {**headers, "Content-Type": "application/json; charset=utf-8"}

# Test contains filter on "Task name" field
filter_body = {
    "field_names": ["Task name", "Status", "Assignee", "Priority"],
    "filter": {
        "conjunction": "or",
        "conditions": [
            {"field_name": "Task name", "operator": "contains", "value": ["数据"]},
        ]
    }
}
url4 = f"{_BASE}/apps/{BITABLE_TOKEN}/tables/{TABLE_ID}/records/search"
resp4 = requests.post(url4, headers=bt_headers, params={"page_size": 10}, json=filter_body, timeout=10)
data4 = resp4.json()
if data4.get("code") != 0:
    print(f"  Filter error: code={data4.get('code')} msg={data4.get('msg')}")
else:
    records = data4.get("data", {}).get("items", [])
    total = data4.get("data", {}).get("total", "?")
    print(f"  'contains 数据' filter: {len(records)} hits (total={total})")
    for r in records[:5]:
        f = r.get("fields", {})
        print(f"    {f.get('Task name','')} | {f.get('Status','')} | {f.get('Priority','')}")
