"""Test Sheet Find API for keyword-based section location"""
import requests, os
from dotenv import load_dotenv
load_dotenv()
UAT = os.getenv('FEISHU_UAT')
headers = {'Authorization': f'Bearer {UAT}', 'Content-Type': 'application/json; charset=utf-8'}
SHEET_TOKEN = 'LC1BsAqWph5DxstTwXYcojsjnic'

def test_find(sheet_id, range_str, keyword):
    url = f'https://open.feishu.cn/open-apis/sheets/v3/spreadsheets/{SHEET_TOKEN}/sheets/{sheet_id}/find'
    body = {
        'find_condition': {
            'range': range_str,
            'match_case': False,
            'match_entire_cell': False,
            'search_by_regex': False,
            'include_formulas': False
        },
        'find': keyword
    }
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    data = resp.json()
    print(f'Find "{keyword}" in {sheet_id}: code={data.get("code")} msg={data.get("msg","")}')
    if data.get('code') == 0:
        fr = data.get('data', {}).get('find_result', {})
        print(f'  matched: {fr.get("matched_cells", 0)} cells')
        for cell in fr.get('matched_cell_data_list', [])[:10]:
            print(f'  → {cell}')
    print()

# Test 1: Find 'KOL' in 营销方案申请表单
test_find('8Je1do', '8Je1do!A1:V35', 'KOL')

# Test 2: Find '库存' in 工作汇总
test_find('12e766', '12e766!A1:R30', '库存')

# Test 3: Find 'Google' in 营销方案申请表单
test_find('8Je1do', '8Je1do!A1:V35', 'Google')

# Test 4: Find '预算' in 营销方案申请表单
test_find('8Je1do', '8Je1do!A1:V35', '预算')
