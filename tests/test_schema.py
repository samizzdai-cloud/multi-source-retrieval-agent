import json
import os
import requests
from dotenv import load_dotenv

load_dotenv()

FEISHU_MCP_ENDPOINT = os.getenv("FEISHU_MCP_ENDPOINT", "https://mcp.feishu.cn/mcp")
FEISHU_UAT = os.getenv("FEISHU_UAT")

payload = {
    "jsonrpc": "2.0",
    "method": "tools/list",
    "id": 1,
    "params": {}
}

# 要查询的工具名称，改这里即可切换不同工具
TOOL_NAME = "fetch-doc"

headers = {
    "Content-Type": "application/json",
    "X-Lark-MCP-UAT": FEISHU_UAT,
    "X-Lark-MCP-Allowed-Tools": "search-doc,fetch-doc",
}

response = requests.post(FEISHU_MCP_ENDPOINT, headers=headers, json=payload, timeout=10)
result = response.json()

tools = result.get("result", {}).get("tools", [])
for tool in tools:
    if tool.get("name") == TOOL_NAME:
        print(f"=== {TOOL_NAME} schema ===")
        print(json.dumps(tool, indent=4, ensure_ascii=False))
        break
else:
    print(f"未找到 {TOOL_NAME}，所有可用工具：")
    print(json.dumps([t.get("name") for t in tools], indent=4, ensure_ascii=False))
