"""
bitable_read.py — 多维表格查询工具

提供四个核心函数（只读）：
  list_tables(bitable_id, uat)                              → 列出所有数据表
  search_records(bitable_id, table_id, uat, conditions)     → 查询记录（is operator）
  search_records_with_filter(bitable_id, table_id, uat, ...) → 查询记录（任意 filter）
  list_fields(bitable_id, table_id, uat)                    → 列出所有字段
"""

import requests
from typing import Optional

_BASE_URL = "https://open.feishu.cn/open-apis/bitable/v1"


def _auth_headers(uat: str) -> dict:
    return {"Authorization": f"Bearer {uat}"}


def list_tables(bitable_id: str, user_access_token: str) -> list[dict]:
    """列出多维表格内所有数据表。

    Args:
        bitable_id:         多维表格 app_token
        user_access_token:  飞书用户访问令牌

    Returns:
        [{"bitable_id": str, "table_id": str, "name": str}, ...]

    Raises:
        RuntimeError: API 返回非 0 错误码
    """
    url = f"{_BASE_URL}/apps/{bitable_id}/tables"
    headers = _auth_headers(user_access_token)
    results = []
    page_token = None

    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            raise RuntimeError(f"list_tables 失败 {body.get('code')}: {body.get('msg')}")

        data = body.get("data") or {}
        for item in data.get("items", []):
            results.append({
                "bitable_id": bitable_id,
                "table_id": item["table_id"],
                "name": item.get("name", ""),
            })

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")

    return results


def search_records(
    bitable_id: str,
    table_id: str,
    user_access_token: str,
    conditions: Optional[list[dict]] = None,
) -> list[dict]:
    """查询数据表记录。

    Args:
        bitable_id:         多维表格 app_token
        table_id:           数据表 ID
        user_access_token:  飞书用户访问令牌
        conditions:         筛选条件列表，格式：
                            [{"field_name": "字段名", "value": ["值"]}]
                            operator 固定为 "is"，conjunction 固定为 "and"
                            默认 None 表示不筛选，返回全部记录

    Returns:
        原始 record 列表，每条包含 record_id 和 fields

    Raises:
        RuntimeError: API 返回非 0 错误码
    """
    url = f"{_BASE_URL}/apps/{bitable_id}/tables/{table_id}/records/search"
    headers = {**_auth_headers(user_access_token), "Content-Type": "application/json; charset=utf-8"}
    results = []
    page_token = None

    # 构造 filter（仅在有 conditions 时传入）
    body_filter = None
    if conditions:
        body_filter = {
            "conjunction": "and",
            "conditions": [
                {"field_name": c["field_name"], "operator": "is", "value": c["value"]}
                for c in conditions
            ],
        }

    while True:
        params = {"page_size": 500}
        if page_token:
            params["page_token"] = page_token

        payload = {}
        if body_filter:
            payload["filter"] = body_filter

        resp = requests.post(url, headers=headers, params=params, json=payload, timeout=10)
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            raise RuntimeError(f"search_records 失败 {body.get('code')}: {body.get('msg')}")

        data = body.get("data") or {}
        results.extend(data.get("items", []))

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")

    return results


def search_records_with_filter(
    bitable_id: str,
    table_id: str,
    user_access_token: str,
    filter_body: Optional[dict] = None,
    field_names: Optional[list[str]] = None,
    page_size: int = 500,
    max_records: int = 1000,
) -> list[dict]:
    """查询数据表记录，支持任意 filter（含 contains / isGreater 等操作符）。

    与 search_records 的区别：
    - filter_body 可传入完整 filter 结构（支持 contains、or 等）
    - search_records 仅支持 is operator + and conjunction

    Args:
        bitable_id:         多维表格 app_token
        table_id:           数据表 ID
        user_access_token:  飞书用户访问令牌
        filter_body:        完整 filter 结构，如
                            {"conjunction": "or", "conditions": [...]}
                            None 表示无过滤，返回全部
        field_names:        返回的字段名列表，None 表示返回全部字段
        page_size:          分页大小，最大 500
        max_records:        最多返回记录数

    Returns:
        原始 record 列表，每条包含 record_id 和 fields

    Raises:
        RuntimeError: API 返回非 0 错误码
    """
    url = f"{_BASE_URL}/apps/{bitable_id}/tables/{table_id}/records/search"
    headers = {**_auth_headers(user_access_token), "Content-Type": "application/json; charset=utf-8"}
    results = []
    page_token = None

    payload: dict = {}
    if filter_body:
        payload["filter"] = filter_body
    if field_names:
        payload["field_names"] = field_names

    while len(results) < max_records:
        params = {"page_size": min(page_size, 500)}
        if page_token:
            params["page_token"] = page_token

        resp = requests.post(url, headers=headers, params=params, json=payload, timeout=15)
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            raise RuntimeError(
                f"search_records_with_filter 失败 {body.get('code')}: {body.get('msg')}"
            )

        data = body.get("data") or {}
        results.extend(data.get("items", []))

        if not data.get("has_more") or len(results) >= max_records:
            break
        page_token = data.get("page_token")

    return results[:max_records]


def list_fields(bitable_id: str, table_id: str, user_access_token: str) -> list[dict]:
    """列出数据表所有字段。

    Args:
        bitable_id:         多维表格 app_token
        table_id:           数据表 ID
        user_access_token:  飞书用户访问令牌

    Returns:
        [{"field_id": str, "field_name": str, "type": int, "ui_type": str,
          "is_primary": bool, "property": dict|None}, ...]

    Raises:
        RuntimeError: API 返回非 0 错误码
    """
    url = f"{_BASE_URL}/apps/{bitable_id}/tables/{table_id}/fields"
    headers = _auth_headers(user_access_token)
    results = []
    page_token = None

    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token

        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        body = resp.json()

        if body.get("code") != 0:
            raise RuntimeError(f"list_fields 失败 {body.get('code')}: {body.get('msg')}")

        data = body.get("data") or {}
        for item in data.get("items", []):
            results.append({
                "field_id":   item["field_id"],
                "field_name": item["field_name"],
                "type":       item["type"],
                "ui_type":    item.get("ui_type", ""),
                "is_primary": item.get("is_primary", False),
                "property":   item.get("property"),
            })

        if not data.get("has_more"):
            break
        page_token = data.get("page_token")

    return results
