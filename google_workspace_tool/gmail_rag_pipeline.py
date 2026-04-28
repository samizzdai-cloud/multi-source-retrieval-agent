import base64
import re
import io
from typing import List, Dict, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

class GmailQueryTool:
    """工具 1：邮件搜索发现工具。"""
    
    @staticmethod
    def execute(query_string: str, creds: Any, max_results: int = 100) -> List[str]:
        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
        thread_ids = []
        page_token = None
        
        try:
            while True:
                response = service.users().threads().list(
                    userId='me',
                    q=query_string,
                    maxResults=min(max_results - len(thread_ids), 500),
                    pageToken=page_token
                ).execute()
                
                threads = response.get('threads', [])
                thread_ids.extend([t['id'] for t in threads])
                
                page_token = response.get('nextPageToken')
                if not page_token or len(thread_ids) >= max_results:
                    break
        except HttpError as e:
            print(f"GmailQueryTool Error: {e}")
            
        return thread_ids[:max_results]


class ThreadFetcherTool:
    """工具 2：会话详情拉取工具。"""
    
    @staticmethod
    def execute(thread_id: str, creds: Any) -> Optional[Dict[str, Any]]:
        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
        try:
            # format='full' 获取完整详情，包括所有 Message 和附件元数据
            thread_data = service.users().threads().get(
                userId='me', id=thread_id, format='full'
            ).execute()
            return thread_data
        except HttpError as e:
            print(f"ThreadFetcherTool Error for {thread_id}: {e}")
            return None


class MessageParserTool:
    """工具 3：单封邮件与附件结构解析工具。"""
    
    @classmethod
    def execute(cls, message: Dict[str, Any]) -> Dict[str, Any]:
        payload = message.get('payload', {})
        headers = payload.get('headers', [])
        
        metadata = {
            "Subject": "",
            "From": "",
            "To": "",
            "Date": ""
        }
        for h in headers:
            name = h.get('name', '')
            if name in metadata:
                metadata[name] = h.get('value', '')
                
        body_texts = {"text/plain": "", "text/html": ""}
        attachments = []
        
        # 递归遍历 parts
        cls._parse_parts(payload.get('parts', []), body_texts, attachments)
        
        # 如果 parts 没解析出文本，尝试从 payload.body 获取
        if not body_texts["text/plain"] and not body_texts["text/html"]:
            if payload.get('body', {}).get('data'):
                mime_type = payload.get('mimeType', 'text/plain')
                decoded = cls._decode_base64url(payload['body']['data'])
                if mime_type in body_texts:
                    body_texts[mime_type] = decoded
                    
        # 优先使用 text/plain，若无则降级使用 text/html
        best_mime = 'text/plain' if body_texts['text/plain'] else 'text/html'
        raw_text = body_texts.get(best_mime, '')
        
        body_text = cls._clean_text(raw_text, best_mime)
        
        return {
            "messageId": message.get('id', ''),
            "metadata": metadata,
            "body_text": body_text,
            "attachments": attachments
        }
        
    @classmethod
    def _parse_parts(cls, parts: List[Dict[str, Any]], body_texts: Dict[str, str], attachments: List[Dict[str, str]]):
        for part in parts:
            filename = part.get('filename', '')
            mime_type = part.get('mimeType', '')
            body = part.get('body', {})
            attachment_id = body.get('attachmentId')
            
            # 1. 如果有附件 (attachmentId 存在)
            if filename and attachment_id:
                attachments.append({
                    "filename": filename,
                    "mimeType": mime_type,
                    "attachmentId": attachment_id
                })
                continue
                
            # 2. 否则是普通正文或内联部分
            data = body.get('data')
            if data:
                decoded = cls._decode_base64url(data)
                if mime_type in body_texts:
                    if body_texts[mime_type]:
                        body_texts[mime_type] += "\n" + decoded
                    else:
                        body_texts[mime_type] = decoded
                    
            # 3. 如果还有子 parts，递归
            if 'parts' in part:
                cls._parse_parts(part['parts'], body_texts, attachments)

    @staticmethod
    def _decode_base64url(data: str) -> str:
        if not data:
            return ""
        try:
            # 补齐 padding
            padding = 4 - (len(data) % 4)
            if padding != 4:
                data += "=" * padding
            data = data.replace("-", "+").replace("_", "/")
            return base64.b64decode(data).decode('utf-8', errors='replace')
        except Exception as e:
            return f"[解码失败: {e}]"

    @staticmethod
    def _clean_html(html_str: str) -> str:
        """简单清理 HTML 标签并尽量保留块级换行，避免文字粘连"""
        text = re.sub(r'<style.*?>.*?</style>', '', html_str, flags=re.DOTALL|re.IGNORECASE)
        text = re.sub(r'<script.*?>.*?</script>', '', text, flags=re.DOTALL|re.IGNORECASE)
        
        # 将常见的换行和块级闭合标签替换为换行符
        text = re.sub(r'<br\s*/?>', '\n', text, flags=re.IGNORECASE)
        text = re.sub(r'</(div|p|h[1-6]|li|blockquote|tr)>', '\n', text, flags=re.IGNORECASE)
        
        # 清除其余所有标签
        text = re.sub(r'<[^>]+>', ' ', text)
        
        # HTML 实体稍微还原一下
        text = text.replace('<', '<').replace('>', '>').replace('&nbsp;', ' ').replace('&', '&')
        return text

    @staticmethod
    def _clean_text(text: str, mime_type: str) -> str:
        if mime_type == 'text/html':
            text = MessageParserTool._clean_html(text)
            
        # 邮件历史引用清洗（Quote Stripping）
        lines = text.split('\n')
        clean_lines = []
        for line in lines:
            line_stripped = line.strip()
            
            # 清理行内多余空格
            line_clean = re.sub(r'\s+', ' ', line_stripped)
            
            if not line_clean:
                clean_lines.append("")
                continue
                
            # 1. 匹配典型的中英文回复头部，如匹配到则截断后续所有历史文本
            # "On Mon, Apr 27... wrote:" 
            # "在2026年...写道："
            # "---- Original Message ----"
            if re.match(r'^(>*\s*)On\s+.*wrote:\s*$', line_clean) or \
               re.match(r'^(>*\s*)在.*写道：\s*$', line_clean) or \
               re.match(r'^(>*\s*)\d{4}年\d{1,2}月\d{1,2}日.*写道：\s*$', line_clean) or \
               re.match(r'^\s*[-_]{3,}\s*Original\s*(Message)?\s*[-_]{3,}\s*$', line_clean, re.IGNORECASE):
                break
                
            # 2. 剔除以 > 开头的单独引用行（常见的纯文本回复格式）
            if line_clean.startswith('>'):
                continue
                
            clean_lines.append(line_clean)
            
        # 移除多余的连续空行
        final_text = '\n'.join(clean_lines).strip()
        final_text = re.sub(r'\n{3,}', '\n\n', final_text)
        return final_text


class AttachmentExtractorTool:
    """工具 4：附件下载与文本提取工具。"""
    
    # 允许直接下载和解析的简单文本附件类型集合（后续可在此处维护扩展）
    ALLOWED_ATTACHMENT_MIME_TYPES = {
        'text/plain',
        'text/csv',
        'text/markdown',
        'application/json',
        'text/html',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/msword',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.ms-excel',
        'application/vnd.ms-excel.sheet.macroEnabled.12',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    }
    
    @classmethod
    def execute(cls, message_id: str, attachment: Dict[str, str], creds: Any) -> str:
        filename = attachment.get('filename', 'unknown_file')
        mime_type = attachment.get('mimeType', '')
        attachment_id = attachment.get('attachmentId')
        
        # 针对不在白名单内的附件类型，直接跳过下载以节省 API 请求
        if mime_type not in cls.ALLOWED_ATTACHMENT_MIME_TYPES:
            return f"[不支持的附件类型或非简单文本文件，已跳过解析: {filename} ({mime_type})]"
        
        service = build('gmail', 'v1', credentials=creds, cache_discovery=False)
        try:
            att_data = service.users().messages().attachments().get(
                userId='me', messageId=message_id, id=attachment_id
            ).execute()
            
            data_b64 = att_data.get('data', '')
            if not data_b64:
                return f"[附件无数据: {filename}]"
                
            padding = 4 - (len(data_b64) % 4)
            if padding != 4:
                data_b64 += "=" * padding
            data_b64 = data_b64.replace("-", "+").replace("_", "/")
            raw_bytes = base64.b64decode(data_b64)
            
            # 路由到高级解析器
            if mime_type in ('application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/msword'):
                from readers.word_reader import _try_extract_word_text
                text = _try_extract_word_text(raw_bytes)
                return text if text else f"[Word解析失败: {filename}]"
            elif mime_type in ('application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.ms-excel', 'application/vnd.ms-excel.sheet.macroEnabled.12'):
                from readers.excel_reader import _try_extract_excel_text
                text = _try_extract_excel_text(raw_bytes)
                return text if text else f"[Excel解析失败: {filename}]"
            elif mime_type == 'application/vnd.openxmlformats-officedocument.presentationml.presentation':
                from readers.pptx_reader import _try_extract_pptx_text
                text = _try_extract_pptx_text(raw_bytes)
                return text if text else f"[PPTX解析失败: {filename}]"
            
            decoded_text = raw_bytes.decode('utf-8', errors='replace')
            
            if mime_type == 'text/html':
                from gmail_rag_pipeline import MessageParserTool
                return MessageParserTool._clean_html(decoded_text)
                
            return decoded_text
                
        except HttpError as e:
            return f"[附件下载失败 {filename}: {e}]"
        except Exception as e:
            return f"[附件解析异常 {filename}: {e}]"


class RAGMarkdownCompilerTool:
    """工具 5：最终 Chunk 组装工具。"""
    
    @staticmethod
    def execute(thread_id: str, parsed_messages: List[Dict[str, Any]]) -> str:
        if not parsed_messages:
            return f"---\nchunk_metadata:\n  chunk_id: \"{thread_id}\"\n  status: \"Empty\"\n---\n"
            
        first_msg_meta = parsed_messages[0]['metadata']
        subject = first_msg_meta.get('Subject', 'No Subject')
        
        md_lines = []
        md_lines.append("---")
        md_lines.append("chunk_metadata:")
        md_lines.append(f"  chunk_id: \"{thread_id}\"")
        md_lines.append(f"  title: \"{subject}\"")
        md_lines.append(f"  message_count: {len(parsed_messages)}")
        md_lines.append("---")
        md_lines.append("")
        md_lines.append(f"# {subject}")
        md_lines.append("")
        
        for i, msg in enumerate(parsed_messages, 1):
            meta = msg['metadata']
            md_lines.append(f"### [第 {i} 轮] From: {meta.get('From', 'Unknown')} | Date: {meta.get('Date', 'Unknown')}")
            md_lines.append(f"**To**: {meta.get('To', 'Unknown')}")
            md_lines.append("")
            
            body = msg['body_text']
            if body:
                md_lines.append(body)
            else:
                md_lines.append("*(No text body)*")
                
            attachments_texts = msg.get('attachments_texts', [])
            if attachments_texts:
                md_lines.append("")
                md_lines.append("**(\ud83d\udc47 附件内容)**")
                for att_text in attachments_texts:
                    md_lines.append("```")
                    md_lines.append(att_text)
                    md_lines.append("```")
                    
            md_lines.append("\n---\n")
            
        return "\n".join(md_lines)


def run_gmail_rag_pipeline(query: str, creds: Any, max_threads: int = 5, output_file: Optional[str] = None) -> List[str]:
    """主流程编排：串联上述所有工具，返回生成的 Markdown chunks 列表。"""
    print(f"1. 正在搜索邮件会话: {query}")
    thread_ids = GmailQueryTool.execute(query, creds, max_results=max_threads)
    print(f"找到 {len(thread_ids)} 个相关的 Thread。")
    
    chunks = []
    
    for idx, thread_id in enumerate(thread_ids, 1):
        print(f"2. 拉取 Thread 详情 ({idx}/{len(thread_ids)}): {thread_id}")
        thread_data = ThreadFetcherTool.execute(thread_id, creds)
        if not thread_data:
            continue
            
        messages_data = thread_data.get('messages', [])
        parsed_messages = []
        
        print(f"3. 解析 {len(messages_data)} 封邮件及并发提取附件...")
        
        for msg in messages_data:
            # 3.1 解析单封邮件结构
            parsed = MessageParserTool.execute(msg)
            
            # 3.2 准备附件下载与解析
            attachments_texts = []
            if parsed['attachments']:
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {
                        executor.submit(AttachmentExtractorTool.execute, parsed['messageId'], att, creds): att['filename']
                        for att in parsed['attachments']
                    }
                    for future in as_completed(futures):
                        filename = futures[future]
                        try:
                            result = future.result()
                            attachments_texts.append(f"--- 附件: {filename} ---\n{result}")
                        except Exception as e:
                            attachments_texts.append(f"--- 附件: {filename} ---\n[提取失败: {e}]")
                            
            parsed['attachments_texts'] = attachments_texts
            parsed_messages.append(parsed)
            
        print(f"4. 组装 Markdown Chunk (Thread {thread_id})")
        # 清除无法写入的 surrogate 字符
        chunk_md = RAGMarkdownCompilerTool.execute(thread_id, parsed_messages)
        chunk_md = chunk_md.encode('utf-8', 'replace').decode('utf-8')
        chunks.append(chunk_md)
        
    if output_file and chunks:
        # 将结果写入文件，不同搜索词不同的保存文件
        import os
        os.makedirs(os.path.dirname(output_file) or '.', exist_ok=True)
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write("\n\n=====\n\n".join(chunks))
        print(f"\n成功提取并保存至: {output_file}")
        
    return chunks

if __name__ == '__main__':
    # 此处供本地简单测试使用
    try:
        from auth_utils import get_credentials
        creds = get_credentials(scopes=["https://www.googleapis.com/auth/gmail.readonly"])
        if creds:
            result_chunks = run_gmail_rag_pipeline("has:attachment", creds, max_threads=1)
            if result_chunks:
                print("\n========== 生成的 RAG Chunk 预览 ==========\n")
                print(result_chunks[0])
                print("\n==========================================\n")
    except ImportError:
        print("未找到 auth_utils.py，跳过测试。")


class RAGChunkCompilerTool:
    """工具 6：GoogleChunk 组装工具（每封邮件一个 chunk）。"""

    @staticmethod
    def to_chunks(thread_id: str, parsed_messages: List[Dict[str, Any]]) -> List:
        """将解析后的邮件列表转为 GoogleChunk 列表（每封邮件一个 chunk）。"""
        from google_workspace_tool.chunkers import GoogleChunk, ChunkContentType, estimate_tokens
        if not parsed_messages:
            return []

        chunks: List[GoogleChunk] = []
        for i, msg in enumerate(parsed_messages, 1):
            meta = msg['metadata']
            subject = meta.get('Subject', 'No Subject')
            sender = meta.get('From', 'Unknown')
            date = meta.get('Date', 'Unknown')
            to = meta.get('To', 'Unknown')
            body = msg.get('body_text', '')

            parts = []
            parts.append(f"**From:** {sender}")
            parts.append(f"**To:** {to}")
            parts.append(f"**Date:** {date}")
            parts.append(f"**Subject:** {subject}")
            parts.append("")
            if body:
                parts.append(body)
            else:
                parts.append("*(No text body)*")

            attachments_texts = msg.get('attachments_texts', [])
            if attachments_texts:
                parts.append("")
                parts.append("**Attachments:**")
                for att_text in attachments_texts:
                    parts.append("```")
                    parts.append(att_text)
                    parts.append("```")

            content = "\n".join(parts)
            chunk_id = f"google:gmail:{thread_id}:{i}"
            thread_url = f"https://mail.google.com/mail/u/0/#inbox/{thread_id}"

            chunks.append(GoogleChunk(
                chunk_id=chunk_id,
                file_id=thread_id,
                file_title=subject,
                file_url=thread_url,
                heading_path=subject,
                content_type=ChunkContentType.TEXT,
                content=content,
                token_count=estimate_tokens(content),
                has_table=False,
                metadata={
                    "mime_type": "message/rfc822",
                    "thread_id": thread_id,
                    "message_index": i,
                    "sender": sender,
                    "date": date,
                },
            ))
        return chunks


def run_gmail_chunk_pipeline(query: str, creds: Any, max_threads: int = 5, enable_attachments: bool = False) -> List:
    """主流程编排：返回 GoogleChunk 列表（每封邮件一个 chunk）。"""
    from google_workspace_tool.chunkers import GoogleChunk
    thread_ids = GmailQueryTool.execute(query, creds, max_results=max_threads)
    all_chunks: List[GoogleChunk] = []

    for thread_id in thread_ids:
        thread_data = ThreadFetcherTool.execute(thread_id, creds)
        if not thread_data:
            continue

        messages_data = thread_data.get('messages', [])
        parsed_messages = []

        for msg in messages_data:
            parsed = MessageParserTool.execute(msg)
            attachments_texts = []
            if enable_attachments and parsed['attachments']:
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {
                        executor.submit(AttachmentExtractorTool.execute, parsed['messageId'], att, creds): att['filename']
                        for att in parsed['attachments']
                    }
                    for future in as_completed(futures):
                        filename = futures[future]
                        try:
                            result = future.result()
                            attachments_texts.append(f"--- 附件: {filename} ---\n{result}")
                        except Exception as e:
                            attachments_texts.append(f"--- 附件: {filename} ---\n[提取失败: {e}]")
            parsed['attachments_texts'] = attachments_texts
            parsed_messages.append(parsed)

        chunks = RAGChunkCompilerTool.to_chunks(thread_id, parsed_messages)
        all_chunks.extend(chunks)

    return all_chunks
