# Google Slides Reader 使用指南与能力对齐分析

本文档提供了针对 `slide_reader.py` 的用法介绍，并横向对齐了 `doc_reader` 与 `sheet_reader` 的能力。同时探讨了在 RAG（Retrieval-Augmented Generation）场景下支持**渐进式阅读**的未来演进路径。

---

## 1. 快速使用指南 (Usage)

`slide_reader.py` 被设计为高并发且智能化的幻灯片抓取管道，可快速还原幻灯片的人类阅读顺序（上下左右），智能组合伪表格并生成下游 RAG 系统友好的 Markdown Chunk。

### 1.1 环境与前置要求
在调用之前，请确保已具备 Google API `Credentials` 对象，并且已授权范围包含（至少）：
- `https://www.googleapis.com/auth/presentations.readonly`
- （可选，依赖其它模块获取元数据）`https://www.googleapis.com/auth/drive.readonly`

### 1.2 核心方法调用

#### A. 读取单个幻灯片 (Read One)
该方法将完整提取指定 `file_id` 的幻灯片，处理其尺寸/坐标、提取讲演者备注、忽略删除线文本以及剔除多余图片占位。
```python
from auth_utils import get_credentials
from readers import slide_reader

# 1. 准备凭证
SCOPES = ["https://www.googleapis.com/auth/presentations.readonly"]
creds = get_credentials(SCOPES)

# 2. 读取并解析单个 Slides
result = slide_reader.read_one(file_id="your_presentation_id_here", creds=creds)

if result.get("error"):
    print("错误：", result["error"])
else:
    # content 中包含了按照每页幻灯片分隔成多块的统一 Markdown 数据
    md_content = result["content"]
    doc_title = result["title"]
    print(f"解析成功，标题：{doc_title}")
    print(md_content)
```

#### B. 并发批量读取 (Read Concurrent)
当您有数十个幻灯片需要提取时，强烈推荐使用并发读取以掩盖冗长的 API 获取延迟。
```python
file_ids = ["id_1", "id_2", "id_3"]
# 设定 max_workers 并发线程数
results = slide_reader.read_concurrent(file_ids, creds=creds, max_workers=5)

for res in results:
    if not res.get("error"):
        print(f"成功提取文件：{res['title']}")
```

### 1.3 输出 Markdown 结构示例
解析器生成的 Chunk 以 `=====` 分隔多页幻灯片，每一页具有如下标准结构：
```markdown
---
chunk_metadata:
  chunk_id: "1dYAL0iODb1x0sJpOQqMhcl0c5hni5OrPN-jl0-3K2x0_slide_1"
  file_url: "https://docs.google.com/presentation/d/.../edit#slide=id..."
  title: "[全局 Presentation 标题]"
---

# 幻灯片内识别的大标题...

## 🎤 核心摘要与演讲者备注 (Speaker Notes)
备注内容...

---
## 🖼️ Elements Position
**[position: y:105]**
| Col 1 | Col 2 |
|---|---|
| 时间线A | 时间线B |
| 里程碑 1 | 里程碑 1 |

**[position: x:64, y:444]**
普通文本内容...
```

---

## 2. 模块能力对齐分析 (Docs vs Sheets vs Slides)

根据现有框架设计与业务场景，对比分析 Slides、Docs 和 Sheets 的能力图谱：

| 提取阶段/特性 | Docs (`doc_reader`) | Sheets (`sheet_reader`) | Slides (`slide_reader`) |
| --- | --- | --- | --- |
| **API 请求策略** | `documents().get` 获取整树 | `values().get` 获取区间/整表 | `presentations().get` 获取整树 |
| **全页解析延迟 (粗估)**| 大文档极慢（长达数十秒）| 快（常规 <2秒） | 较快（常规 <5秒，因不涉及深层 DOM） |
| **坐标与定位还原** | 自然的文档流式排版 | (行, 列) 的自然网格分布 | **坐标矩阵 $x, y$ 绝对计算与重排** |
| **伪表格转换** | 通过段落与表格自带结构还原 | 无需伪表格，自带网格结构 | **多列 Y 轴对齐文本自动切割组装为表格** |
| **讲演者/备注/附注** | N/A | N/A | **从关联的 `notesPage` 精确定位** |
| **图片降级处理** | 直接跳过忽略 | 跳过忽略 | **忽略修饰图片，保留 SheetsChart 描述** |
| **删除线忽略** | `strikethrough: true` 过滤 | 暂不处理内联富文本样式 | `strikethrough: true` 过滤 |
| **并发读取** | 支持 (`read_concurrent`) | 支持 (`read_concurrent`) | 支持 (`read_concurrent`) |
| **精确定位 URL** | 提供 Heading 锚点支持 (规划中) | 提供 Sheet GID 锚点支持 | **已支持精确 `file_url#slide=id.xxx` 锚点** |

### 2.1 工具能力配备结论
- **数据拉取与封装：** 目前三大解析器均统一配备了 `read_one`、`read_batch` 和 `read_concurrent`。
- **空间/阅读顺序感知：** Slides Reader 是唯一深入使用**仿射变换（AffineTransform）**并在本地进行投影排序和伪表格组装的模块，成功解决了演示文稿无序画布带来的内容混溯问题。
- **Chunk Metadata 对齐：** 随着 RAG 需求的推进，Slides 已经率先实现了极简纯净的 Chunk 级 Metadata。

---

## 3. 渐进式阅读与后续演进探讨 (Progressive Reading & Evolution)

当前由于所有模块几乎都是依赖于单次获取（Fetch-All）再本地处理的模式，在遇到特大文档或幻灯片页数极多的场景下将受到网络传输时长的限制。以下为演进方案分析：

### 3.1 我们能提前感知 Slides 中的元数据和页面大纲吗？
- **当前局限**：调用 `presentations().get` 默认返回含有大量排版信息的整棵树（JSON）。虽然我们可以要求 API 只返回 `fields="title, slides(objectId)"` 来提前得知有几页、各页的标题，但若用户想看具体某页的内容，我们依然需要重新向 API 发起请求，这样会导致重复的网络延迟。
- **是否能提取 Summary？**：Google Slides 本身并未提供文档维度的 Summary，但在每页关联的 `speakerNotesObjectId`（讲演者备注）具备极高的摘要价值，这也是当前我们将其作为全局上下文置于页首的原因。

### 3.2 渐进式阅读实现方案 (Caching-Based Outline & Zoom-in)
若需要在后续定制**渐进式阅读（Progressive Reading）**，最佳实践如下：
1. **统一获取与缓存 (Fetch & Cache)**：
   一次性调用 `presentations().get` 获取全量 JSON，但并不立刻解析为几万字的 Markdown。而是将此高昂代价的原始 JSON 缓存到本地磁盘/内存中。
2. **生成摘要与大纲 (Outline Mode)**：
   提供快速大纲解析器，仅从缓存的 JSON 提取每张 Slide 的 `Slide_ID` 和 `TITLE`，在瞬间（几毫秒）内生成如下的大纲视图供用户阅读：
   > 1. 第一页：发布会时间线
   > 2. 第二页：竞品对比分析
3. **精细阅读 (Zoom-in/Section Mode)**：
   当用户在全局搜索或者大纲中锁定了某一页后，调用针对性的函数 `render_slide(cached_json, target_slide_id)`。该方法在本地迅速完成该单页的空间解析、伪表格处理，零网络延迟返回结果。
   
### 3.3 全文搜索时的锚点跳转
- **已配备**：现在的 Chunk Metadata 中自带了形如 `https://docs.google.com/presentation/d/.../edit#slide=id.g327...` 的链接。只要上游的向量数据库（Vector DB）记录了这个 `file_url`，用户提问得到大模型回答时，引用的来源可以直接一键跳转至 Google Slides 的特定一页，实现与原始编辑平台的完美对齐。