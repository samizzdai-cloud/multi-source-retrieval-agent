# Plan: Google Slides 结构化读取与 RAG Chunk 转换模块

> 目标：重构 `slide_reader.py`，实现基于“Google Slides 转化为高质量 RAG Chunk”的 Vibe Coding 架构设计方案，重点提取结构化文本、删除线过滤和空间布局还原，同时保障性能与并发处理能力。

---

## 1. 核心需求与架构梳理

Google Slides 的内容本质是分散在不同页面（Page）中的画布元素（PageElements）。为了生成高质量的 RAG Chunk，我们需要构建一个 5 个阶段的管道（Pipeline）。

### 1.1 环节一：数据拉取层 (Data Fetching Layer)

- **目标**：一次性获取演示文稿的完整结构化 JSON。
- **操作**：使用 `presentations.get` (无需额外字段过滤，一次性拉回整棵树)。
- **输出**：`presentationId` 和 `slides[]` 数组。每个 Slide 对应一个最终的 RAG Chunk。
- **并发与性能**：参考 Docs 和 Sheets，API Fetch 是瓶颈。需要保留串行、并发两种模式，记录耗时进行性能基准测试。

### 1.2 环节二：全局上下文与备注提取 (Global Context & Notes Extraction)

- **目标**：提取演讲者备注 (Speaker Notes)，作为 Chunk 的核心全局上下文。
- **操作**：
  - 读取当前 Slide 的 `slideProperties.notesPage` 对象。
  - 获取 `notesProperties.speakerNotesObjectId`。
  - 在 `notesPage.pageElements` 中定位对应的 Shape，并提取文本。
- **注意**：部分 Slide 可能没有备注。

### 1.3 环节三：空间坐标解析与重排推断 (Spatial Parsing & Layout Inference)

- **目标**：打破元素的创建顺序限制，恢复为人类阅读习惯（从上到下、左到右），并推断隐含的排布。
- **操作**：
  - 基于 `size` 和 `transform` (AffineTransform) 矩阵。
  - **坐标计算**：通过矩阵公式 $x' = scale_x \times x + shear_x \times y + translate_x$ 计算 Bounding Box。
  - **组装与对齐容差**：设置一定的容差判定同行内容（Y轴对齐），优先按 Y 升序，X 升序排序。
  - （可选高级：伪表格推断，对非表格元素执行空间排序）。

### 1.4 环节四：核心页面元素语义抽取 (Semantic Parsing of Elements)

- **目标**：按不同 `element_kind` 执行精细化提取。
- **文本简化还原与删除线过滤**：
  - **标题锚点**：检查 `shape.placeholder.type` (TITLE, CENTERED_TITLE, SUBTITLE)。对于缺乏标题的页面，优先尝试使用左上角、面积最大的文本块。
  - **文本抽取**：通过 `textRun.content` 提取内容。通过 `bullet.nestingLevel` 还原列表缩进。
  - **删除线过滤**：检查 `textRun.style.strikethrough`，若为 `true` 则直接丢弃文本块，不进入最终 Chunk。
  - **样式还原简配**：不深度还原字体/加粗等样式，只保留必要的阅读层级结构（列表标记、标题等级）。
- **逻辑组合 (`elementGroup`)**：
  - 递归提取 `children`，并确保组内元素按相对关系一起处理。计算绝对坐标时需累乘父级矩阵。
- **原生表格 (`table`)**：
  - 遍历 `tableRows` 和 `tableCells`。
  - 输出为 Markdown 表格。暂简化处理复杂的合并单元格（以内容为主）。
- **视觉元素 (`image` / `sheetsChart`)**：
  - 提取 `title` 和 `description` (Alt Text)，降级为文本：`[Image/Chart: {title} - {description}]`。

### 1.5 环节五：最终的 Markdown 呈现形式 (Final Presentation format)

- **目标**：拼装为目标下游（Vector DB/Agent）友好的 RAG Chunk 模板，并且元数据和表格提取符合标准 Markdown 以支持无缝的全局检索。
- **优化策略**：
  1. **伪表格自动转置**：如果同一 Y 坐标附近存在多个水平分布的元素块且其内部包含换行，系统会按照换行符自动切分各块，随后组合转置为真正的多行 Markdown 表格。
  2. **图片忽略**：纯粹的修饰性图片元素 `[Image]` 会被略过以提升文本纯度；仅图表等携带分析意义的附件会降级附带其文字描述输出。
  3. **精简 Metadata**：在每页幻灯片的开头提供标准 Yaml 的 `chunk_metadata` 以记录 chunk 身份、精确跳转链接及所属幻灯片标题，便于直接传递给 RAG 的源追踪模块。

- **模板示例**：
```markdown
---
chunk_metadata:
  chunk_id: "{presentationId}_slide_{number}"
  file_url: "https://docs.google.com/presentation/d/{presentationId}/edit#slide=id.{slideId}"
  title: "[全局 Presentation 标题]"
---

# [Slide Title: 提取自 placeholder.type == TITLE 的文本，如果没找到则用 "无标题页面"]

## 🎤 核心摘要与演讲者备注 (Speaker Notes)
*(通过 notesProperties.speakerNotesObjectId 提取绑定的文本内容)*
...备注内容...

## 🖼️ Elements Position
*(按 Y/X 投影对齐的阅读顺序，原本各自跨行的左右分栏组合成的伪表格)*

**[position: y:105]**
| 栏目A 第一行 | 栏目B 第一行 |
|---|---|
| 数据A 1 | 数据B 1 |
| 数据A 2 | 数据B 2 |

**[position: x:101, y:439]**
[SheetsChart: "Q3营收柱状图" - "展示了各地区营收对比"]

**[position: x:50, y:388]**
普通段落内容...
```

---

## 2. API 与数据结构细节

### 2.1 文本与删除线检测

```json
"textElements": [
  {
    "textRun": {
      "content": "被删除的文字",
      "style": {
        "strikethrough": true
      }
    }
  }
]
```
**逻辑**：在遍历 `textElements` 时，如果 `textRun.style.strikethrough == True`，则 `continue` 忽略。

### 2.2 矩阵计算公式与单位

`transform` 矩阵结构：
```json
"transform": {
  "scaleX": 1.2,
  "scaleY": 1.2,
  "shearX": 0,
  "shearY": 0,
  "translateX": 2500000,
  "translateY": 1500000,
  "unit": "EMU"
}
```
`size` 结构：
```json
"size": {
  "width": { "magnitude": 3000000, "unit": "EMU" },
  "height": { "magnitude": 1000000, "unit": "EMU" }
}
```
**坐标计算**：
将左上角 `(0,0)` 映射：
$x_0 = translateX$
$y_0 = translateY$

右下角 `(width, height)` 映射：
$x_1 = scaleX \times width + shearX \times height + translateX$
$y_1 = scaleY \times height + shearY \times width + translateY$

这提供了该 Element 的 Bounding Box $(x_0, y_0, x_1, y_1)$。

---

## 3. 测试与验证计划

如同 `doc_reader` 和 `sheet_reader`，我们需要：
1. **测试用例选择**：选择包含图表、备注、带有删除线文本、不同排版布局（左右分栏）的 Google Slides。
2. **正确性验证**：
   - 是否准确抛弃了删除线文本？
   - 左右两栏的文本是否按照 "先上后下、先左后右" 的人类阅读顺序被提取，而非原始 API 返回顺序（可能先右后左）？
   - 备注是否正确提取并位于 Chunk 头部？
3. **并发与延迟压测**：
   - 测量单文件读取耗时（API Fetch Time vs Parse Time）。
   - 测量批量读取 10 个 Slides 的耗时。
   - 比较并发模式下（5/10 workers）的性能提升比例。

## 4. 后续任务分配 (Act Mode 待执行)

1. [ ] 在 `Google_retrieve_tools/readers/slide_reader.py` 中引入空间解析器、元素提取器和新的组装逻辑。
2. [ ] 编写测试脚本，使用真实的 Google Slides（包含特殊布局、备注和删除线）执行测试。
3. [ ] 记录测试性能并输出评估结果。
