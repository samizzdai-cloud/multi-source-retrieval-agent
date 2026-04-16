"""
docx_fetch_content.py — 飞书文档内容摘要获取

提供两种方式获取文档前 500 字摘要：
  fetch_docx_raw_content  — docx 新版文档，通过纯文本 API
  fetch_doc_block_summary — doc 旧版文档，通过 block API 提取
"""

import requests

SUMMARY_MAX_LEN = 500


def fetch_docx_raw_content(document_id: str, user_access_token: str) -> str:
    """获取 docx 文档纯文本内容的前 500 字。

    Args:
        document_id:        文档 ID
        user_access_token:  飞书访问令牌

    Returns:
        文档纯文本前 500 字，失败返回空字符串
    """
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/raw_content"
    headers = {"Authorization": f"Bearer {user_access_token}"}

    try:
        resp = requests.get(url, headers=headers, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if body.get("code") != 0:
            return ""
        content = body.get("data", {}).get("content", "")
        return content[:SUMMARY_MAX_LEN]
    except Exception:
        return ""


def _extract_block_text(block: dict) -> str:
    """从单个 block 提取纯文本（兼容 SDK 对象和字典）。"""
    type_to_attr = {
        2: "text", 3: "heading1", 4: "heading2", 5: "heading3",
        6: "heading4", 7: "heading5", 8: "heading6",
        9: "heading7", 10: "heading8", 11: "heading9",
        12: "bullet", 13: "ordered", 14: "quote", 17: "todo",
    }
    block_type = block.get("block_type", 0)
    attr = type_to_attr.get(block_type, "text")
    text_obj = block.get(attr) or block.get("text")
    if not text_obj:
        return ""

    elements = text_obj.get("elements", []) if isinstance(text_obj, dict) else []
    parts = []
    for el in elements:
        if isinstance(el, dict):
            tr = el.get("text_run")
            if tr and isinstance(tr, dict):
                parts.append(tr.get("content", ""))
    return "".join(parts)


def fetch_doc_block_summary(document_id: str, user_access_token: str) -> str:
    """通过 block API 获取 doc/wiki 文档摘要（前 500 字）。

    提取策略：
      1. 扫描所有 block，检查存在哪些 heading 类型
      2. 优先取前 2 个 heading1(type=3) 下的 text(type=2) 内容
      3. 无 heading1 则取 heading2(type=4)
      4. 都没有则按序取所有 text block 内容
      5. 截断到 500 字

    Args:
        document_id:        文档 ID
        user_access_token:  飞书访问令牌

    Returns:
        文档摘要前 500 字，失败返回空字符串
    """
    url = f"https://open.feishu.cn/open-apis/docx/v1/documents/{document_id}/blocks"
    headers = {"Authorization": f"Bearer {user_access_token}"}

    all_blocks = []
    page_token = None
    try:
        while True:
            params = {"page_size": 500}
            if page_token:
                params["page_token"] = page_token
            resp = requests.get(url, headers=headers, params=params, timeout=10)
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                return ""
            data = body.get("data", {})
            all_blocks.extend(data.get("items", []))
            if not data.get("has_more"):
                break
            page_token = data.get("page_token")
    except Exception:
        return ""

    if not all_blocks:
        return ""

    existing_types = {b.get("block_type") for b in all_blocks}

    heading_type = None
    if 3 in existing_types:
        heading_type = 3
    elif 4 in existing_types:
        heading_type = 4

    text_parts = []

    if heading_type:
        heading_count = 0
        collecting = False
        for block in all_blocks:
            bt = block.get("block_type", 0)
            if bt == heading_type:
                heading_count += 1
                if heading_count > 2:
                    break
                collecting = True
                t = _extract_block_text(block)
                if t:
                    text_parts.append(t)
            elif collecting and bt == 2:
                t = _extract_block_text(block)
                if t:
                    text_parts.append(t)
    else:
        for block in all_blocks:
            bt = block.get("block_type", 0)
            if bt == 2:
                t = _extract_block_text(block)
                if t:
                    text_parts.append(t)

    result = "\n".join(text_parts)
    return result[:SUMMARY_MAX_LEN]
