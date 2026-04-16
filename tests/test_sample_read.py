"""读取用户提供的 Sheet 和 Bitable 样本，分析真实数据结构"""

import json
import os
import sys
import requests
from dotenv import load_dotenv

load_dotenv()
UAT = os.getenv("FEISHU_UAT", "")

# ── Sheet 样本 ──────────────────────────────────────────────

SHEET_TOKEN = "LC1BsAqWph5DxstTwXYcojsjnic"
SHEET_ID = "8Je1do"


def read_sheet_sample():
    """读取 sheet 样本: 先获取元数据，再读取全部数据"""
    print("=" * 70)
    print("  SHEET SAMPLE")
    print("=" * 70)

    # Step 1: 获取所有 sheet tabs 的元数据
    url = f"https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{SHEET_TOKEN}/sheets/query"
    headers = {"Authorization": f"Bearer {UAT}"}
    resp = requests.get(url, headers=headers, timeout=15)
    data = resp.json()

    if data.get("code") != 0:
        print(f"  Metadata error: {data.get('msg')}")
        return

    sheets = data.get("data", {}).get("sheets", [])
    print(f"\n  Total tabs: {len(sheets)}")
    for s in sheets:
        grid = s.get("grid_properties", {})
        print(f"    Tab: {s.get('title', '?')} (id={s.get('sheet_id')}) "
              f"rows={grid.get('row_count', '?')} cols={grid.get('column_count', '?')} "
              f"type={s.get('resource_type', '?')} hidden={s.get('hidden', '?')}")

    # Step 2: 读取指定 sheet tab 的数据
    target_sheet = None
    for s in sheets:
        if s.get("sheet_id") == SHEET_ID:
            target_sheet = s
            break

    if not target_sheet:
        print(f"  Sheet ID {SHEET_ID} not found")
        return

    grid = target_sheet.get("grid_properties", {})
    row_count = min(grid.get("row_count", 100), 100)  # cap at 100 rows for inspection
    col_count = grid.get("column_count", 26)

    # Build range
    def num_to_col(n):
        result = ""
        n += 1
        while n > 0:
            n -= 1
            result = chr(ord('A') + n % 26) + result
            n //= 26
        return result

    end_col = num_to_col(col_count - 1)
    range_str = f"{SHEET_ID}!A1:{end_col}{row_count}"
    print(f"\n  Reading range: {range_str}")

    url2 = (f"https://open.feishu.cn/open-apis/sheets/v2/spreadsheets"
            f"/{SHEET_TOKEN}/values/{range_str}?valueRenderOption=FormattedValue")
    resp2 = requests.get(url2, headers=headers, timeout=15)
    data2 = resp2.json()

    if data2.get("code") != 0:
        print(f"  Read error: {data2.get('msg')}")
        return

    values = data2.get("data", {}).get("valueRange", {}).get("values", [])
    print(f"  Rows returned: {len(values)}")

    # Print all rows with row numbers to see structure
    print(f"\n  ── Full Data ({len(values)} rows × {col_count} cols) ──")
    for i, row in enumerate(values):
        # Stringify cells, truncate long ones
        cells = []
        for cell in (row or []):
            if cell is None:
                cells.append("")
            else:
                s = str(cell).replace("\n", "\\n")
                cells.append(s[:40] + "..." if len(s) > 40 else s)
        # Pad to col_count
        while len(cells) < col_count:
            cells.append("")
        print(f"  R{i+1:3d}: {' | '.join(cells[:min(col_count, 15)])}")

    # Analyze structure: find empty rows, header-like rows
    print(f"\n  ── Structure Analysis ──")
    empty_rows = []
    header_candidates = []
    for i, row in enumerate(values):
        if not row or all(c is None or str(c).strip() == "" for c in row):
            empty_rows.append(i + 1)
        else:
            non_empty = sum(1 for c in row if c is not None and str(c).strip())
            non_numeric = sum(1 for c in row if c is not None and str(c).strip()
                             and not str(c).strip().replace(",", "").replace(".", "").replace("%", "").replace("-", "").isdigit())
            if non_empty > 0 and non_numeric / max(non_empty, 1) > 0.7:
                header_candidates.append(i + 1)

    print(f"  Empty rows: {empty_rows[:20]}{'...' if len(empty_rows) > 20 else ''}")
    print(f"  Header-like rows (>70% text): {header_candidates[:20]}")


# ── Bitable 样本 ─────────────────────────────────────────────

BITABLE_TOKEN = "S4swbS3G9a74nmsYeKJcjIo6nnh"
BITABLE_TABLE_ID = "tbl7hZD9CLvShHWr"
BITABLE_VIEW_ID = "vew1C7ARBu"
_BASE = "https://open.feishu.cn/open-apis/bitable/v1"


def read_bitable_sample():
    print("\n\n" + "=" * 70)
    print("  BITABLE SAMPLE")
    print("=" * 70)

    headers = {"Authorization": f"Bearer {UAT}"}

    # Step 1: List all tables
    url = f"{_BASE}/apps/{BITABLE_TOKEN}/tables"
    resp = requests.get(url, headers=headers, params={"page_size": 100}, timeout=10)
    data = resp.json()
    if data.get("code") != 0:
        print(f"  list_tables error: {data.get('msg')}")
        return
    tables = data.get("data", {}).get("items", [])
    print(f"\n  Total tables: {len(tables)}")
    for t in tables:
        print(f"    Table: {t.get('name', '?')} (id={t.get('table_id')})")

    # Step 2: List fields for target table
    url2 = f"{_BASE}/apps/{BITABLE_TOKEN}/tables/{BITABLE_TABLE_ID}/fields"
    resp2 = requests.get(url2, headers=headers, params={"page_size": 100}, timeout=10)
    data2 = resp2.json()
    if data2.get("code") != 0:
        print(f"  list_fields error: {data2.get('msg')}")
        return
    fields = data2.get("data", {}).get("items", [])
    print(f"\n  Fields in table '{BITABLE_TABLE_ID}': {len(fields)}")
    field_names = []
    for f in fields:
        fname = f.get("field_name", "?")
        ftype = f.get("type", "?")
        ui_type = f.get("ui_type", "")
        is_primary = f.get("is_primary", False)
        field_names.append(fname)
        print(f"    {fname} (type={ftype}, ui={ui_type}, primary={is_primary})")

    # Step 3: Search records (first 20, no filter)
    url3 = f"{_BASE}/apps/{BITABLE_TOKEN}/tables/{BITABLE_TABLE_ID}/records/search"
    headers3 = {**headers, "Content-Type": "application/json; charset=utf-8"}
    resp3 = requests.post(url3, headers=headers3,
                          params={"page_size": 20},
                          json={"field_names": field_names},
                          timeout=10)
    data3 = resp3.json()
    if data3.get("code") != 0:
        print(f"  search_records error: code={data3.get('code')} msg={data3.get('msg')}")
        return
    records = data3.get("data", {}).get("items", [])
    total = data3.get("data", {}).get("total", "?")
    print(f"\n  Records: {len(records)} fetched (total={total})")

    # Print records as table
    print(f"\n  ── Records ──")
    # Header
    print(f"  {'  |  '.join(fn[:20] for fn in field_names[:10])}")
    print(f"  {'--+--'.join('-' * 20 for _ in field_names[:10])}")

    for rec in records[:20]:
        fields_data = rec.get("fields", {})
        cells = []
        for fn in field_names[:10]:
            val = fields_data.get(fn, "")
            # Handle complex field values (person, link, etc.)
            if isinstance(val, list):
                parts = []
                for v in val:
                    if isinstance(v, dict):
                        parts.append(v.get("text", v.get("name", str(v)[:30])))
                    else:
                        parts.append(str(v)[:30])
                val_str = ", ".join(parts)
            elif isinstance(val, dict):
                val_str = val.get("text", val.get("name", str(val)[:30]))
            else:
                val_str = str(val) if val is not None else ""
            cells.append(val_str[:20])
        print(f"  {'  |  '.join(cells)}")


if __name__ == "__main__":
    if not UAT:
        print("ERROR: FEISHU_UAT not set")
        sys.exit(1)
    read_sheet_sample()
    read_bitable_sample()
