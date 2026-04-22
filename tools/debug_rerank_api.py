"""调试 Rerank API 调用"""

import os
import requests
import json
from pathlib import Path
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_ROOT / ".env", override=True)

DASHSCOPE_API_KEY = os.getenv("DASHSCOPE_API_KEY", "")

if not DASHSCOPE_API_KEY:
    print("❌ 未配置 DASHSCOPE_API_KEY")
    exit(1)

# 测试数据
query = "NUBIA 在美国市场的销售表现"
documents = [
    "[NUBIA 2024 Q1 销售数据分析] 本文档详细分析了 NUBIA 品牌在 2024 年第一季度的销售表现",
    "[NUBIA 产品规格说明书] NUBIA 手机产品的技术规格和参数说明",
    "[NUBIA 美国市场运营报告] NUBIA 在美国市场的运营情况报告",
]

# 构造请求
endpoint = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"
headers = {
    "Authorization": f"Bearer {DASHSCOPE_API_KEY}",
    "Content-Type": "application/json",
}

payload = {
    "model": "qwen3-rerank",
    "query": query,
    "documents": documents,
    "top_n": 3,
    "instruct": "Given a web search query, retrieve relevant passages that answer the query.",
}

print("="*80)
print("调试 Rerank API 调用")
print("="*80)
print(f"\nEndpoint: {endpoint}")
print(f"\nPayload:")
print(json.dumps(payload, ensure_ascii=False, indent=2))

# 发送请求
print(f"\n发送请求...")
response = requests.post(endpoint, headers=headers, json=payload, timeout=30)

print(f"\nHTTP Status: {response.status_code}")
print(f"\n原始响应:")
print(json.dumps(response.json(), ensure_ascii=False, indent=2))

# 解析响应
data = response.json()
if "output" in data:
    output = data["output"]
    results = output.get("results", [])
    print(f"\n解析结果:")
    print(f"  results 数量: {len(results)}")
    for i, item in enumerate(results, 1):
        print(f"  {i}. index={item.get('index')}, score={item.get('relevance_score'):.4f}")
else:
    print(f"\n⚠️  响应中没有 'output' 字段")
    print(f"响应keys: {list(data.keys())}")
