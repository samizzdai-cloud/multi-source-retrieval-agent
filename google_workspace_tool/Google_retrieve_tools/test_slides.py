"""
测试 Google Slides 结构化读取与 RAG Chunk 生成。
验证并发读取、空间排序、删除线过滤和备注提取。
"""

import os
import time
from auth_utils import get_credentials
from drive_search_tool import search_drive
from readers import slide_reader

SCOPES = [
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/presentations.readonly",
    "https://www.googleapis.com/auth/documents.readonly",
    "https://www.googleapis.com/auth/spreadsheets.readonly"
]

def find_test_slides(creds, limit=5):
    """搜索最近的几个 Google Slides 文件。"""
    print(">>> 正在搜索最近的 Google Slides 文件...")
    # mimeType 为 application/vnd.google-apps.presentation
    query = "mimeType='application/vnd.google-apps.presentation'"
    
    files = search_drive(
        q=query,
        page_size=limit,
        order_by="modifiedTime desc",
        creds=creds,
        fields="nextPageToken, files(id, name, mimeType, modifiedTime)"
    )
    
    return files

def main():
    creds = get_credentials(SCOPES)
    
    # 1. 强制测试指定文件 ID
    target_id = "1dYAL0iODb1x0sJpOQqMhcl0c5hni5OrPN-jl0-3K2x0"
    file_id = target_id
    file_name = "test_feedback_slides"
    
    # 2. 单文件解析测试 (包含性能计时)
    print("\n=============================================")
    print(f">>> 开始解析用户指定的 Slides 文件 (ID: {file_id}) ...")
    
    print(f"正在读取: {file_name} ...")
    start_time = time.time()
    
    # 调用 slide_reader
    result = slide_reader.read_one(file_id, creds=creds)
    
    elapsed = time.time() - start_time
    
    if result.get("error"):
        print(f"读取失败: {result['error']}")
    else:
        content = result.get("content", "")
        print(f"读取成功! 耗时: {elapsed:.2f}s")
        print(f"内容总长度: {len(content)} 字符")
        print("\n--- 输出预览 (前 2000 个字符) ---")
        print(content[:2000])
        print("---------------------------------")
        
        # 写入本地文件以供人工详细检查
        output_dir = os.path.join(os.path.dirname(__file__), "test_results")
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, f"feedback_{file_name}.md")
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"\n完整 Markdown 已保存至: {out_path}")

if __name__ == "__main__":
    main()
