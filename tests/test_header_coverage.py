"""深入研究表头检测：列覆盖度匹配 + 连续非空区域的结构识别

核心假设：表头的非空列位置 应该和后续数据行的非空列位置 高度重叠。
如果某行的非空列位置和后续行不匹配 → 它是说明文字/标题，不是表头。

同时分析：连续非空区域（无空行分隔）如何检测结构边界。
"""
import requests, os, re
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

def read_range(sheet_id, max_row, max_col):
    range_str = f"{sheet_id}!A1:{num_to_col(max_col-1)}{max_row}"
    url = (f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets"
           f"/{SHEET_TOKEN}/values/{range_str}?valueRenderOption=FormattedValue")
    resp = requests.get(url, headers=headers, timeout=15)
    return resp.json().get('data', {}).get('valueRange', {}).get('values', [])

def get_filled_cols(row, max_col=None):
    """返回一行中非空 cell 的列索引集合"""
    if not row:
        return set()
    cols = set()
    for j, cell in enumerate(row):
        if cell is not None and str(cell).strip():
            cols.add(j)
    return cols

def fill_count(row):
    return len(get_filled_cols(row))

def coverage_ratio(header_cols, data_cols):
    """数据行的非空列 被表头覆盖的比例。
    即 data_cols 中有多少列在 header_cols 中也有值。
    """
    if not data_cols:
        return 0.0
    overlap = header_cols & data_cols
    return len(overlap) / len(data_cols)

def header_coverage_score(candidate_row, subsequent_rows, n=5):
    """计算候选表头与后续 n 行数据的平均列覆盖度。"""
    header_cols = get_filled_cols(candidate_row)
    if not header_cols:
        return 0.0
    scores = []
    for row in subsequent_rows[:n]:
        data_cols = get_filled_cols(row)
        if data_cols:  # skip empty rows
            scores.append(coverage_ratio(header_cols, data_cols))
    return sum(scores) / len(scores) if scores else 0.0


# ══════════════════════════════════════════════════════════════════
# Test 1: 营销方案申请表单 — form-like layout
# ══════════════════════════════════════════════════════════════════
print("=" * 70)
print("  TEST 1: 营销方案申请表单 (8Je1do) — 列覆盖度分析")
print("=" * 70)

values1 = read_range('8Je1do', 35, 22)
print(f"\n  {'ROW':>5}  fill  filled_cols                coverage_with_next_5")
print(f"  {'-'*70}")

for i, row in enumerate(values1):
    fc = fill_count(row)
    filled = sorted(get_filled_cols(row))
    col_names = [num_to_col(c) for c in filled]
    
    # Compute coverage with next 5 non-empty rows
    subsequent = [r for r in values1[i+1:i+10] if fill_count(r) > 0][:5]
    cov = header_coverage_score(row, subsequent)
    
    label = ""
    if fc == 0:
        label = "── empty ──"
    elif cov >= 0.7 and fc >= 4:
        label = "★ HEADER (high coverage + dense)"
    elif cov >= 0.7 and fc >= 2:
        label = "◆ sub-header (high coverage)"
    elif fc == 1:
        label = "title/label"
    elif cov < 0.3 and fc >= 2:
        label = "✗ desc text (low coverage)"
    
    if fc > 0:
        print(f"  R{i+1:3d}  {fc:4d}  {','.join(col_names):30s}  cov={cov:.2f}  {label}")
    else:
        print(f"  R{i+1:3d}  {fc:4d}  {'':30s}  {label}")


# ══════════════════════════════════════════════════════════════════
# Test 2: 工作汇总 — 连续非空区域的结构分析
# ══════════════════════════════════════════════════════════════════
print(f"\n\n{'='*70}")
print("  TEST 2: 工作汇总 (12e766) — 连续非空区域结构分析")
print("=" * 70)

values2 = read_range('12e766', 25, 18)
print(f"\n  {'ROW':>5}  fill  filled_cols                coverage_with_next_5   col_pattern_change")
print(f"  {'-'*80}")

prev_cols = set()
for i, row in enumerate(values2):
    fc = fill_count(row)
    filled = sorted(get_filled_cols(row))
    col_names = [num_to_col(c) for c in filled]
    subsequent = [r for r in values2[i+1:i+10] if fill_count(r) > 0][:5]
    cov = header_coverage_score(row, subsequent)
    
    # Detect column pattern change from previous row
    curr_cols = get_filled_cols(row)
    if prev_cols and curr_cols:
        pattern_diff = len(curr_cols.symmetric_difference(prev_cols)) / max(len(curr_cols | prev_cols), 1)
    else:
        pattern_diff = 1.0
    prev_cols = curr_cols if fc > 0 else prev_cols
    
    label = ""
    if fc == 0:
        label = "── empty ──"
    elif cov >= 0.7 and fc >= 4:
        label = "★ HEADER"
    elif cov >= 0.7 and fc >= 2:
        label = "◆ sub-header"
    elif fc == 1:
        label = "title/label"
    
    if fc > 0:
        print(f"  R{i+1:3d}  {fc:4d}  {','.join(col_names):30s}  cov={cov:.2f}  Δ={pattern_diff:.2f}  {label}")
    else:
        print(f"  R{i+1:3d}  {fc:4d}  {'':30s}  {label}")


# ══════════════════════════════════════════════════════════════════
# Test 3: 多个 Tab 的 coverage 快速扫描
# ══════════════════════════════════════════════════════════════════
print(f"\n\n{'='*70}")
print("  TEST 3: 多 Tab 快速 coverage 扫描 (前5行)")
print("=" * 70)

# Get metadata
url = f'https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{SHEET_TOKEN}/sheets/query'
resp = requests.get(url, headers=headers, timeout=15)
sheets = resp.json().get('data', {}).get('sheets', [])

for s in sheets[:8]:
    sid = s.get('sheet_id')
    title = s.get('title', '?')
    hidden = s.get('hidden', False)
    if hidden:
        continue
    grid = s.get('grid_properties', {})
    rc = min(grid.get('row_count', 50), 15)
    cc = min(grid.get('column_count', 20), 20)
    
    vals = read_range(sid, rc, cc)
    
    print(f"\n  Tab: {title} ({sid})")
    # Find first row with fill ≥ 4 — likely header
    for i, row in enumerate(vals[:10]):
        fc = fill_count(row)
        if fc >= 3:
            subsequent = [r for r in vals[i+1:i+8] if fill_count(r) > 0][:5]
            cov = header_coverage_score(row, subsequent)
            filled = sorted(get_filled_cols(row))
            col_str = ','.join(num_to_col(c) for c in filled[:8])
            print(f"    R{i+1}: fill={fc} cols=[{col_str}] cov={cov:.2f} {'★ HEADER' if cov >= 0.6 else ''}")


# ══════════════════════════════════════════════════════════════════
# Test 4: 验证覆盖度假设 — 对比说明文字 vs 真正表头
# ══════════════════════════════════════════════════════════════════
print(f"\n\n{'='*70}")
print("  TEST 4: 覆盖度假设验证")
print("=" * 70)

# In 营销方案申请表单:
# R4 (4 fill) 是子区域标题，R13 (14 fill) 是真正表头
# R4 后面跟的是 R5(空), R6(不同pattern)
# R13 后面跟的是 R14, R15 (数据行)

print("\n  营销方案申请表单:")
r4 = values1[3]   # R4: 海外发布时间线 | Teasing | Launch | Open sales
r6 = values1[5]   # R6: 海外产品介绍 | 产品参数 | Message house | KSP | 价格区间
r13 = values1[12]  # R13: 产品 | 类别 | 名称和链接 | ...
r17 = values1[16]  # R17: ADs | 预算总包 | 月度预算
r26 = values1[25]  # R26: 主题 | 素材 | 发送站点 | 发布时间 | FL内容

# R4 coverage with what follows
r4_subsequent = [r for r in values1[4:12] if fill_count(r) > 0][:5]
r4_cov = header_coverage_score(r4, r4_subsequent)

# R13 coverage with R14, R15
r13_subsequent = [r for r in values1[13:20] if fill_count(r) > 0][:5]
r13_cov = header_coverage_score(r13, r13_subsequent)

# R17 coverage with R18, R19
r17_subsequent = [r for r in values1[17:22] if fill_count(r) > 0][:5]
r17_cov = header_coverage_score(r17, r17_subsequent)

# R26 coverage with what follows
r26_subsequent = [r for r in values1[26:32] if fill_count(r) > 0][:3]
r26_cov = header_coverage_score(r26, r26_subsequent)

print(f"    R4  (海外发布时间线, fill=4):  cov with next data = {r4_cov:.2f}")
print(f"    R13 (产品|类别|..., fill=14): cov with next data = {r13_cov:.2f}")
print(f"    R17 (ADs|预算总包, fill=3):   cov with next data = {r17_cov:.2f}")
print(f"    R26 (主题|素材|..., fill=5):   cov with next data = {r26_cov:.2f}")

print("\n  工作汇总:")
r1 = values2[0]  # R1: DTC托管运营服务 (title)
r2 = values2[1]  # R2: 域|类型|工作描述|Fastlane|ZTE|备注 (header)

r1_subsequent = [r for r in values2[1:8] if fill_count(r) > 0][:5]
r1_cov = header_coverage_score(r1, r1_subsequent)

r2_subsequent = [r for r in values2[2:10] if fill_count(r) > 0][:5]
r2_cov = header_coverage_score(r2, r2_subsequent)

print(f"    R1 (DTC托管运营服务, fill=1): cov with next data = {r1_cov:.2f}")
print(f"    R2 (域|类型|工作描述, fill=6): cov with next data = {r2_cov:.2f}")

print("\n  CONCLUSION:")
print(f"    说明文字/标题 coverage: R4={r4_cov:.2f}, R1={r1_cov:.2f}")
print(f"    真正表头 coverage:     R13={r13_cov:.2f}, R2={r2_cov:.2f}, R17={r17_cov:.2f}")
print(f"    子表头 coverage:       R26={r26_cov:.2f}")
