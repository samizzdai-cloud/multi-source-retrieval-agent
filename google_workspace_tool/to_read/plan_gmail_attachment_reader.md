# Gmail 附件解析插件化架构设计 (Plugin-based Attachment Reader Plan)

## 1. 架构目标
目前 `gmail_rag_pipeline.py` 中的附件提取逻辑高度耦合，这不仅限制了对复杂办公文档（如 PDF, DOCX）的扩展支持，也使得工具显得臃肿。
为解决此问题，我们将**解析器 (Reader) 与主流程 (Pipeline) 彻底解耦**。未来的架构中：
- **Reader (解析器)**：仅负责将特定的文件流（或文件 ID）解析并转化为标准的 Markdown Chunk。
- **Pipeline (主流程)**：负责搜索邮件、拉取详情、提取正文，并作为入口将复杂附件“路由”给对应的 Reader，最终收集并组合 Markdown Chunks 供下游消费。

---

## 2. 核心设计：策略模式 + 动态导入 (Dynamic Import)

将 `AttachmentExtractorTool` 改造为一个轻量级的 **“附件路由分发器 (Router / Dispatcher)”**。它不再包含具体的 PDF 或 Word 解析逻辑，而是根据附件的 `mimeType`，动态加载 `Google_retrieve_tools/readers/` 目录下的专用阅读器模块。

### 2.1 依赖关系反转
- 原本：Pipeline 包含所有依赖探测代码（如 `import PyPDF2`）。
- 现在：Pipeline 只进行 MIME 映射和流传递。所有的依赖隔离在具体的 `readers/*.py` 插件内部。

### 2.2 动态按需加载逻辑示例
在重构 `AttachmentExtractorTool` 时，我们将采用以下逻辑模式：

```python
import importlib

class AttachmentExtractorTool:
    # 简单文本附件白名单（直接解析）
    SIMPLE_TEXT_MIMES = {'text/plain', 'text/csv', 'text/markdown', 'application/json', 'text/html'}
    
    # 复杂文档解析器映射表
    COMPLEX_PARSER_MAP = {
        'application/pdf': 'readers.pdf_reader',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document': 'readers.word_reader',
        # 未来可扩展：
        # 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet': 'readers.sheet_reader',
        # 'application/vnd.openxmlformats-officedocument.presentationml.presentation': 'readers.slide_reader'
    }

    @classmethod
    def execute(cls, message_id: str, attachment: Dict[str, str], creds: Any) -> str:
        mime_type = attachment.get('mimeType', '')
        
        # 1. 简单文本：直接通过 Base64 解码返回
        if mime_type in cls.SIMPLE_TEXT_MIMES:
            return cls._parse_simple_text(message_id, attachment, creds)
            
        # 2. 复杂文档：插件化路由分发
        if mime_type in cls.COMPLEX_PARSER_MAP:
            module_name = cls.COMPLEX_PARSER_MAP[mime_type]
            try:
                # 动态加载对应的 reader 插件
                reader_module = importlib.import_module(module_name)
                # 获取附件二进制流
                raw_bytes = cls._download_attachment_bytes(message_id, attachment, creds)
                
                # 约定插件必须提供统一的解析接口，例如：
                # reader_module.read_from_bytes(raw_bytes) -> dict(content="...", structured_md="...")
                result = reader_module.read_from_bytes(raw_bytes)
                
                if result.get('error'):
                    return f"[解析报错: {result['error']}]"
                return result.get('structured_md') or result.get('content') or ""
                
            except ImportError:
                return f"[缺少本地解析插件或依赖无法加载: {module_name}]"
            except Exception as e:
                return f"[插件执行失败: {e}]"
                
        # 3. 其他不可解析类型：降级占位符
        return f"[不支持的附件类型或视觉/二进制文件，已跳过: {attachment.get('filename')} ({mime_type})]"
```

---

## 3. Readers 插件接口契约 (Reader Contract)

为了让上述动态路由生效，`Google_retrieve_tools/readers/` 目录下的各类文件（如 `pdf_reader.py`, `word_reader.py`）需要提供统一的字节流解析入口（之前这些 Reader 可能只支持传入 Drive `file_id` 进行下载）。

**改造要求**：
在现有的 `reader.py` 中增加直接接收字节流的方法：
```python
def read_from_bytes(raw_bytes: bytes) -> dict:
    """
    统一的解析契约
    Args:
        raw_bytes: 文件原始二进制数据
    Returns:
        dict: 必须包含 'content' (纯文本) 或 'structured_md' (Markdown结构) 字段，
              如果失败则返回包含 'error' 字段的字典。
    """
    pass
```

---

## 4. 架构优势总结
1. **极致解耦**：`gmail_rag_pipeline.py` 彻底回归其“调度者”的本质，专注于邮件树（Thread）的遍历、正文提取和上下文拼装。
2. **渐进式升级**：下游可以直接接收组装好的 Markdown Chunks。如果未来用户需要支持 Excel，只需在 `readers/` 目录下增加 `sheet_reader.py` 并在 `COMPLEX_PARSER_MAP` 中添加一行映射即可。
3. **隐私与环境安全**：一切皆在本地按需发生。不想解析某种格式，只需不安装该模块的特定 Python 依赖即可，框架会自动优雅降级，不会导致主程序崩溃。