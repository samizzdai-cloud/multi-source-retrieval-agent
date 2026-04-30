import asyncio
import logging
import random
import time
from typing import List, Dict, Tuple

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from google_workspace_tool.auth_utils import get_credentials
from tools.google.google_retrieve import _SCOPES
from google_workspace_tool.drive_space_lister import list_drive_spaces_async
from google_workspace_tool.readers.doc_reader import read_one
from tools.core.llm_client import call_llm

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def _fetch_folder_contents_sync(creds, folder_id: str, max_items: int):
    # 为每个线程创建独立的 service 实例，解决 SIGSEGV 线程安全问题
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    query = f"'{folder_id}' in parents and trashed = false"
    response = service.files().list(
        q=query,
        spaces="drive",
        fields="files(id, name, mimeType)",
        pageSize=max_items,
        supportsAllDrives=True,
        includeItemsFromAllDrives=True
    ).execute()
    return response.get("files", [])

async def get_folder_contents(creds, folder_id: str, max_items: int = 10):
    """获取单个文件夹下的文件和子文件夹，限制数量"""
    try:
        files = await asyncio.to_thread(_fetch_folder_contents_sync, creds, folder_id, max_items)
        return files
    except Exception as e:
        logger.warning(f"Error fetching folder {folder_id}: {e}")
        return []

async def build_tree_concurrently(creds, root_id: str, max_depth: int = 4, current_depth: int = 1) -> Tuple[str, List[str]]:
    """并发递归构建树结构"""
    if current_depth > max_depth:
        return "", []

    indent = "  " * current_depth
    tree_text = ""
    docs = []

    files = await get_folder_contents(creds, root_id, max_items=10)
    
    sub_folder_tasks = []
    sub_folder_names = []
    
    for f in files:
        name = f.get("name", "Unknown")
        mime = f.get("mimeType", "")
        fid = f.get("id")
        
        if mime == "application/vnd.google-apps.folder":
            sub_folder_tasks.append(build_tree_concurrently(creds, fid, max_depth, current_depth + 1))
            sub_folder_names.append(name)
        elif mime == "application/vnd.google-apps.document":
            tree_text += f"{indent}- 📄 {name}\n"
            docs.append(fid)
        else:
            tree_text += f"{indent}- 📎 {name}\n"

    if sub_folder_tasks:
        results = await asyncio.gather(*sub_folder_tasks)
        for name, (sub_tree, sub_docs) in zip(sub_folder_names, results):
            tree_text += f"{indent}- 📁 {name}\n"
            tree_text += sub_tree
            docs.extend(sub_docs)

    return tree_text, docs

async def fetch_doc_content(creds, doc_id: str) -> str:
    """单独抓取文档内容的协程"""
    try:
        result = await asyncio.to_thread(
            read_one,
            file_id=doc_id,
            creds=creds,
            supports_all_drives=True
        )
        content = result.get("content", "")
        if content:
            return content[:800].replace('\n', ' ')
    except Exception as e:
        logger.warning(f"Failed to read doc {doc_id}: {e}")
    return ""

async def generate_profile():
    creds = get_credentials(scopes=_SCOPES)
    service = build("drive", "v3", credentials=creds, cache_discovery=False)
    
    logger.info("Fetching top-level spaces...")
    start_time = time.perf_counter()
    spaces = await list_drive_spaces_async(creds)
    
    full_tree_text = "Google Drive 目录架构样本：\n\n"
    all_doc_ids = []
    
    target_spaces = spaces[:10]  # 处理前10个根空间
    
    logger.info(f"Building tree concurrently for {len(target_spaces)} spaces...")
    
    tree_tasks = []
    for s in target_spaces:
        space_id = s.get('id')
        tree_tasks.append(build_tree_concurrently(creds, space_id, max_depth=4))
        
    tree_results = await asyncio.gather(*tree_tasks)
    
    for s, (tree, docs) in zip(target_spaces, tree_results):
        space_name = s.get('name', 'Unknown Space')
        full_tree_text += f"==== 根空间：{space_name} [{s.get('space_type')}] ====\n"
        full_tree_text += tree
        full_tree_text += "\n"
        all_doc_ids.extend(docs)
        
    logger.info(f"Tree built in {time.perf_counter() - start_time:.2f}s. Found {len(all_doc_ids)} docs.")
    
    # 随机抽取 3 个 docs
    sampled_docs = random.sample(all_doc_ids, min(3, len(all_doc_ids)))
    docs_text = ""
    
    if sampled_docs:
        logger.info("Concurrently reading sampled docs...")
        doc_tasks = [fetch_doc_content(creds, doc_id) for doc_id in sampled_docs]
        doc_contents = await asyncio.gather(*doc_tasks)
        
        for content in doc_contents:
            if content:
                docs_text += f"--- 文档片段样本 ---\n{content}...\n\n"

    prompt = f"""你是 RAG 系统的架构师。请根据以下探测到的 Google Workspace (Drive) 目录树和抽样 Docs，总结出一段精炼的 Instruction 画像（限 50-100 字内，必须一次性说完）。
告诉路由规划器，这个 Google 环境主要沉淀了哪些业务领域的内容、什么类型的文件、适合回答哪些问题。

**绝对禁止**：在生成的画像中包含任何具体的品牌名、公司名或项目名（如 REDMAGIC, ZTE, nubia, Hypershell, Nothing 等），必须抽象为通用名词（如“某消费电子品牌”、“特定竞品”等），以保证这是一段完全脱敏的指导说明。

请直接使用中立陈述句输出结果，禁止换行和寒暄。

【目录树骨架】
{full_tree_text[:6000]} # 防止过长

【文档内容样本】
{docs_text}
"""

    logger.info("Calling LLM to generate profile...")
    try:
        profile_text = await asyncio.to_thread(
            call_llm,
            prompt,
            model="gemini-3.1-pro-preview",
            max_tokens=4000
        )
        profile_text = profile_text.strip()
        
        output_path = "google_workspace_tool/google_profile_instruction.txt"
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(profile_text)
            
        logger.info(f"Successfully generated profile:\n{profile_text}\nSaved to {output_path}")
        
    except Exception as e:
        logger.error(f"Failed to generate profile: {e}")

if __name__ == "__main__":
    asyncio.run(generate_profile())
