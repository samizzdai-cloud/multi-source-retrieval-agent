"""测试 MCP fetch-doc 分页翻完后内容覆盖率，专门检查 whiteboard / bitable / sheet"""
import sys, os, requests, time
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from dotenv import load_dotenv
load_dotenv(override=True)
from feishu_read_tool.client import FeishuClient
from feishu_read_tool.basic_tool.mcp_fetch_doc import fetch_doc_markdown
from feishu_read_tool.chunker import _extract_block_text, HEADING_TYPES, TEXT_TYPES
from feishu_read_tool.table_handler import _extract_cell_text
from feishu_read_tool.basic_tool.bitable_read import list_tables, search_records
from feishu_read_tool.basic_tool.utils import extract_text_field

UAT = os.getenv('FEISHU_UAT', '')
client = FeishuClient(user_access_token=UAT)
HEADERS = {'Authorization': f'Bearer {UAT}'}
DOC_ID = 'AFi3dXgZqo2YMVxQ7QBcjb10nug'  # wiki doc, total_length=82308

# ── Step 1: 逐页拼接完整 MCP 内容 ─────────────────────────────────
print('=== Step 1: MCP fetch-doc 分页翻完 ===')
PAGE_SIZE = 20000
t0 = time.perf_counter()
pages = []
offset = 0
total_len = 0
while True:
    r = fetch_doc_markdown(DOC_ID, uat=UAT, limit=PAGE_SIZE, offset=offset)
    if r['status'] != 'success':
        print(f'  ❌ offset={offset} failed: {r.get("message")}')
        break
    pages.append(r['content'])
    total_len = r.get('total_length', 0)
    has_more = r.get('has_more', False)
    print(f'  page offset={offset:6d}: chars={r["char_count"]:6d}  total_len={total_len}  has_more={has_more}')
    if not has_more:
        break
    offset += PAGE_SIZE

full_mcp = ''.join(pages)
fetch_ms = (time.perf_counter() - t0) * 1000
print(f'\n翻页完成: {len(pages)} pages  fetched_chars={len(full_mcp)}  total_length={total_len}  {fetch_ms:.0f}ms')
print(f'覆盖率: {len(full_mcp)/total_len*100:.1f}%  差距: {total_len - len(full_mcp)} chars')

# ── Step 2: 全部 block 内容 vs 完整 MCP ────────────────────────────
print('\n=== Step 2: Block type 覆盖率（全页拼接后）===')
blocks = client.list_document_blocks(DOC_ID)
bdict = {b['block_id']: b for b in blocks}

NAMES = {2:'text',3:'H1',4:'H2',5:'H3',6:'H4',12:'bullet',13:'ordered',
         14:'quote',15:'code',17:'todo',19:'callout',22:'divider',
         30:'sheet_embed',31:'table',43:'whiteboard'}

def in_full(text):
    check = text.strip()[:20]
    return bool(check and len(check) >= 4 and check in full_mcp)

results = {}
for b in blocks:
    bt = b.get('block_type', 0)
    if bt in (1, 24, 25, 27, 32, 34, 40, 53): continue
    if bt not in results:
        results[bt] = {'found': 0, 'total': 0, 'miss': [], 'hit': []}
    rv = results[bt]

    if bt in HEADING_TYPES or bt in TEXT_TYPES:
        t = _extract_block_text(b)
        if t.strip() and len(t.strip()) >= 5:
            rv['total'] += 1
            if in_full(t):
                rv['found'] += 1
            else:
                if len(rv['miss']) < 2: rv['miss'].append(t.strip()[:50])

    elif bt == 19:
        child_texts = []
        for cid in b.get('children', []):
            ct = _extract_block_text(bdict.get(cid, {}))
            if ct.strip(): child_texts.append(ct.strip())
        if child_texts:
            rv['total'] += 1
            if in_full(child_texts[0]):
                rv['found'] += 1
            else:
                if len(rv['miss']) < 2: rv['miss'].append(child_texts[0][:50])

    elif bt == 31:
        for cid in b.get('children', [])[:6]:
            cell = bdict.get(cid, {})
            ct = _extract_cell_text(cell, bdict)
            if ct and len(ct.strip()) >= 3:
                rv['total'] += 1
                if in_full(ct):
                    rv['found'] += 1
                else:
                    if len(rv['miss']) < 2: rv['miss'].append(ct.strip()[:50])
                break

    elif bt == 43:
        board_token = b.get('board_token')
        if not board_token: continue
        resp = requests.get(
            f'https://open.feishu.cn/open-apis/board/v1/whiteboards/{board_token}/nodes',
            headers=HEADERS, timeout=10)
        nodes = resp.json().get('data', {}).get('nodes', [])
        for node in nodes:
            if node.get('type') == 'connector': continue
            t_obj = node.get('text')
            t = (t_obj.get('text', '') if isinstance(t_obj, dict) else '') if t_obj else ''
            if not t:
                for para in node.get('rich_text', {}).get('paragraphs', []):
                    for el in para.get('elements', []):
                        tel = el.get('text_element')
                        if tel: t += tel.get('text', '')
            t = ' '.join(t.split())
            if t and len(t) >= 3:
                rv['total'] += 1
                if in_full(t):
                    rv['found'] += 1
                else:
                    if len(rv['miss']) < 2: rv['miss'].append(t[:50])

print(f'{"Block type":<22} {"ok/total":>10}  {"cover":>7}  status')
print('-' * 65)
for bt, stat in sorted(results.items()):
    if stat['total'] == 0: continue
    name = NAMES.get(bt, f'type-{bt}')
    pct = stat['found'] / stat['total'] * 100
    status = '✅ 完整' if pct >= 95 else ('⚠️  部分' if pct >= 50 else '🔴 缺失')
    print(f'{name:<22} {stat["found"]:>4}/{stat["total"]:<5}  {pct:>5.0f}%   {status}')
    for m in stat['miss'][:2]:
        print(f'    ❌ {repr(m)}')

# ── Step 3: 多维表格 bitable (type-18) ─────────────────────────────
print('\n=== Step 3: 多维表格 bitable (type-18) ===')
t18 = [b for b in blocks if b.get('block_type') == 18]
print(f'type-18 blocks in this doc: {len(t18)}')
if t18:
    for b in t18[:2]:
        token = b.get('bitable_token')
        if not token:
            print(f'  block {b["block_id"]}: no bitable_token extracted')
            continue
        try:
            tables = list_tables(token, UAT)
            print(f'  bitable_token={token}: {len(tables)} tables')
            if tables:
                tid = tables[0]['table_id']
                records = search_records(token, tid, UAT)
                print(f'    table[0] id={tid}: {len(records)} records')
                if records:
                    first_fields = records[0].get('fields', {})
                    for fname, fval in list(first_fields.items())[:3]:
                        ftext = extract_text_field(fval)
                        if ftext:
                            found = in_full(ftext)
                            icon = '✅' if found else '❌'
                            print(f'    [{icon}] "{fname}": {repr(ftext[:40])}')
        except Exception as e:
            print(f'  ERROR: {e}')
else:
    print('  该文档无 type-18 blocks（wiki 文档本身没有 bitable embed）')
    # 改用另一篇有 bitable embed 的文档做测试（如果存在）
    print('  → 用 docx 文档测试 bitable embed')

# ── Step 4: sheet_embed (type-30) ─────────────────────────────────
print('\n=== Step 4: sheet_embed (type-30) ===')
t30 = [b for b in blocks if b.get('block_type') == 30]
print(f'type-30 blocks: {len(t30)}')
# MCP 里真正的表格行（至少3个 | 且含 --- 分隔行）
real_table_rows = [l for l in full_mcp.split('\n')
                   if l.count('|') >= 3 and l.strip().startswith('|')]
separator_rows = [l for l in real_table_rows if '---' in l]
print(f'  MCP 内真正的 Markdown 表格行: {len(real_table_rows)}  分隔行(---): {len(separator_rows)}')
if real_table_rows:
    # 打印前几行
    for line in real_table_rows[:4]:
        print(f'    {repr(line[:80])}')
else:
    print('  ❌ MCP 内无真正的 Markdown 表格（sheet 数据未渲染）')

# 检查 sheet token 是否在 MCP 里（链接形式）
if t30:
    sheet = t30[0].get('sheet')
    token = None
    if sheet:
        token = getattr(sheet, 'token', None) or (sheet.get('token') if isinstance(sheet, dict) else None)
    if token:
        in_content = token in full_mcp
        print(f'  sheet token {token}: {"✅ 在 MCP 里(链接)" if in_content else "❌ 不在 MCP 里"}')

# ── Step 5: 对比单次大 limit 与多页翻页的差异 ─────────────────────
print('\n=== Step 5: 单次 limit=300000 vs 逐页翻完 ===')
r_single = fetch_doc_markdown(DOC_ID, uat=UAT, limit=300000, offset=0)
single_chars = r_single.get('char_count', 0)
paged_chars = len(full_mcp)
print(f'  单次 limit=300000: chars={single_chars}  has_more={r_single.get("has_more")}')
print(f'  逐页翻完 ({len(pages)} pages):  chars={paged_chars}')
print(f'  差异: {paged_chars - single_chars} chars ({(paged_chars - single_chars)/total_len*100:.1f}% 额外)')
if paged_chars == single_chars:
    print('  ✅ 单次 limit=300000 已能获取全部内容（与翻页结果相同）')
else:
    print(f'  ⚠️  翻页比单次多 {paged_chars - single_chars} chars')
