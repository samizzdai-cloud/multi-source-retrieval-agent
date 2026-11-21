"""Source Query Config — SourceAgent plan_only LLM 的专业规则配置。

v2 职责（瘦身后）：
  1. 百炼 L2 tag 枚举（``BAILIAN_L2_TAGS``）
  2. LLM tag 输出纠错（``fix_tag``）
  3. 各源 query 编写规则（``SOURCE_QUERY_RULES``）——供 SourceAgent 的 plan_only prompt 注入
  4. 国家缩写展开规则（``COUNTRY_ABBREV_RULE``）——bailian 专用
  5. 品牌路由配置（``BRAND_ROUTING``）——供 Global Planner 参考
  6. L2/时效 tag 硬截断（``enforce_tag_caps``）——bailian SourceAgent 产出兜底

**已删除**（v1 Global Planner 专用，不再需要）：
  - ``build_source_query_rules_text`` / ``build_tags_rules_text``
  - ``build_routing_rules_text`` / ``build_required_targets_rule_text``
  - ``build_multi_entity_strategy_text`` / ``SOURCE_EXPLICIT_INDICATORS``
  - ``validate_and_fix_plan``（依赖已删除的 SubQueryTask）
"""

import logging
import re
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# ── 百炼 l2 子业务领域枚举（固定，与百炼 tag 一致）─────────────────────────

BAILIAN_L2_TAGS = [
    "Product_Launch_Management", "Meeting__Collaboration",
    "Marketplace", "DTC", "Affiliate_Marketing",
    "Sales_Tracking__Inventory", "Logistics", "Procurement__Planning",
    "Social_Media__Subscriptions", "KOL", "Ads", "PR",
    "Private_Sale__Community", "Customer_Experience",
    "Market_Intelligence", "Contracts__Compliance", "Customer_Report",
]

_L2_SET = set(BAILIAN_L2_TAGS)

# 时效 tag 正则
TIME_TAG_RE = re.compile(r'^(last_long|\d{4}_Q[1-4])$')
_TIME_TAG_RE = TIME_TAG_RE  # 旧名兼容


# ── Tag Fuzzy Fix（LLM 常见输出错误 → 正确枚举值）───────────────────────────

_TAG_FUZZY_MAP: Dict[str, str] = {
    "social media": "Social_Media__Subscriptions",
    "social_media": "Social_Media__Subscriptions",
    "social media & subscriptions": "Social_Media__Subscriptions",
    "subscriptions": "Social_Media__Subscriptions",
    "affiliate": "Affiliate_Marketing",
    "affiliate marketing": "Affiliate_Marketing",
    "marketplace": "Marketplace",
    "ads": "Ads",
    "dtc": "DTC",
    "kol": "KOL",
    "pr": "PR",
    "logistics": "Logistics",
    "customer experience": "Customer_Experience",
    "customer_experience": "Customer_Experience",
    "market intelligence": "Market_Intelligence",
    "market_intelligence": "Market_Intelligence",
    "sales tracking": "Sales_Tracking__Inventory",
    "sales_tracking": "Sales_Tracking__Inventory",
    "inventory": "Sales_Tracking__Inventory",
    "procurement": "Procurement__Planning",
    "procurement & planning": "Procurement__Planning",
    "product launch": "Product_Launch_Management",
    "product_launch": "Product_Launch_Management",
    "meeting": "Meeting__Collaboration",
    "meeting & collaboration": "Meeting__Collaboration",
    "contracts": "Contracts__Compliance",
    "contracts & compliance": "Contracts__Compliance",
    "compliance": "Contracts__Compliance",
    "private sale": "Private_Sale__Community",
    "private_sale": "Private_Sale__Community",
    "community": "Private_Sale__Community",
    "customer report": "Customer_Report",
    "customer_report": "Customer_Report",
}


def fix_tag(raw_tag: str) -> Optional[str]:
    """修正 LLM 输出的 tag：精确 → 大小写 → fuzzy → 时效格式 → 丢弃。"""
    if not raw_tag or not isinstance(raw_tag, str):
        return None
    tag = raw_tag.strip()

    if tag in _L2_SET:
        return tag

    if TIME_TAG_RE.match(tag):
        return tag

    for valid in BAILIAN_L2_TAGS:
        if tag.lower() == valid.lower():
            return valid

    normalized = tag.lower().replace("_", " ").replace("-", " ").strip()
    if normalized in _TAG_FUZZY_MAP:
        return _TAG_FUZZY_MAP[normalized]

    relaxed = tag.replace("-", "_").replace(" ", "_")
    if TIME_TAG_RE.match(relaxed):
        return relaxed

    logger.debug("fix_tag: cannot fix '%s', discarding", raw_tag)
    return None


def enforce_tag_caps(tags: List[str]) -> List[str]:
    """bailian tag 硬约束：同 dimension 不能冲突 → L2 ≤ 1 + 时效 ≤ 1。

    百炼 tag 过滤在「同一维度内 OR、跨维度 AND」下，一次 query 里最多 1 个 L2 + 1 个时效；
    LLM 偶尔会产出 2-3 个 L2 导致 AND 语义错乱零召回。本函数做最后兜底。
    """
    if not tags:
        return []
    fixed = [fix_tag(t) for t in tags]
    fixed = [t for t in fixed if t]
    l2_kept: List[str] = []
    time_kept: List[str] = []
    for t in fixed:
        if TIME_TAG_RE.match(t):
            if not time_kept:
                time_kept.append(t)
        else:
            if not l2_kept:
                l2_kept.append(t)
    return l2_kept + time_kept


# ── 跨源/跨阶段共享：实体保真原则 ─────────────────────────────────────────
# 在 Global Planner / 各源 plan_only / 各源 plan_eval 注入，统一拒识"自创实体"。

ENTITY_FIDELITY_BLOCK = """【实体保真原则】
- **不得引入** user_query / focus 中没有明确出现的具体产品名、型号、品牌、竞品、地区限定。
  · 用户问"某品牌销量"，禁止完全替换/扩展为任一具体型号。
  · 用户问"竞品格局"，禁止自创未被用户提及的竞品品牌，或需要进一步验证。
  · 用户问"某品牌销售业务"，禁止收缩到未被用户指定的具体产品线。
- **泛指保留泛指**：产品范围等限定完整保留在 Focus，不能擅自收缩到具体型号。
  API query 可省略不利于召回的泛称和动作词，按目标材料的表达方式转译，不改变 Focus 的范围。
  用户业务问法可映射到行业通用字段或数据来源名称，不能据此补造具体产品/品牌实体。
- source_profile 列出的索引名/文件夹名仅作为路由参考，**不能据此推断用户在问那些具体实体**。
- focus 拆分是**逻辑切面**（如 "产品" / "销售" / "渠道" / "竞品"）的拆分，不是范围扩大或收缩。
- sub_query 是为了从源中**检索得更准**，不是为了**限定到某条产品线**。"""


# ── 共享 sub_query 编写规则（从 feishu/google 抽出的重复段）────────────────
# 在 Global Planner prompt 顶部注入一次；SourceAgent v1 plan_only 通过
# get_source_query_rules(include_common=True) 拼接到 per-source 尾部，保持单源
# prompt 自包含。

COMMON_SUB_QUERY_HINTS = """【共享 sub_query 编写规则】（当前对 feishu / google 生效）

	【高级时间推断（time_filter）】当前系统时间 = {now_str}。
	  · "YYYY 年规划/计划"：规划往往在前一年 Q3/Q4 写 → time_filter 提前 1-3 个月
	    （例：问"2026 年规划" → '>2025-09-01'）
	  · "YYYY 年终总结/复盘"：设为该年 11 月起（例：问"2025 年终复盘" → '>2025-11-01'）
	  · "YYYY年M月" / "M月中旬" / "M月下旬"：仅设左端点 >YYYY-MM-01 或 >YYYY-MM-15，
	    不设右端点（例："2026年6月" → '>2026-06-01'，"6月下旬" → '>2026-06-15'）
	  · 近期/最近 → 留空或 "last_30_days"（按各源支持决定）
	  · 不涉及时效 → 留空

【身份归属（author_filter）】query 明确含"我写的/我创建的/我的/我最近的工作"等归属语
→ author_filter='me'；否则 null。"""


QUERY_TYPE_DESCRIPTION = """【query_type】（feishu / google 必填，决定下游路由）
  · structured：问结构化、可核对、可追踪的事实——指标/数字/排名/销量/GMV/转化率/库存,
                或流程/项目管理/进度/owner/里程碑/任务状态/排期/checklist
                → feishu 开 Table API 深度解析；google 应配 time_filter 收紧范围
  · concept   ：问策略/原因/背景/定位/市场分析/方法论等文本理解
                → 不加 time_filter 扩大召回
  · personal_recent：focus 极其空泛 + 高度个人化（"我最近做了啥"）
                → 同时 author_filter='me'；feishu 走纯 MCP 极速通道；
                  google 强制 google_order_by='viewedByMeTime desc'

同一 focus 既要结构化事实又要背景解释 → 拆两条 sub_query，禁止混合 type。"""


# ── 各源 Query 编写规则（仅源特有部分；共享段在外注入）────────────────────

SOURCE_QUERY_RULES: Dict[str, str] = {
    "feishu": (
        "【Feishu 专属规则 — 关键词检索，query 质量直接决定召回】\n"
        "  【目标内容建模】围绕 Focus 的信息需求，推断能支撑它的材料会如何表述："
        "正文、章节和表格可能使用哪些术语、字段、关系或数据来源名称，再转译为平台可匹配的关键词。"
        "不能只摘抄问题或 Focus，也不预设资料中已有完整答案。标题类型只作辅助。\n"
        "  【查询优化】保留定位业务对象所需的限定，选择少量有辨识度、能共同定位目标内容的词。"
        "优先短查询，避免把全部验收条件和多个抽象业务词塞进同一请求；词数和句式不是固定公式。"
        "根据业务理解、适用 Profile 和已读材料，可将中文问法映射为行业术语、英文原始字段或系统缩写。"
        "同一 Focus 可用不同表达寻找同类证据，第二条请求应有实际召回价值。\n"
        "  【范围保真】完整验收条件保留在 Focus；查询优化不改变业务含义。"
        "仅品牌时不猜当前核心型号；显式型号可用核实过的别名。Profile 提供理解与用词线索，不是证据。\n"
        "  【硬约束】user_query 中的具体数值（如 0.5、379、43.1%）不是检索词。"
        "每条 sub_query 最多保留一个数值，且数值必须绑定其指标名。"
        "其余数值保留为 Focus 的证据验收条件。\n"
        "  【硬约束】时间表达（如 '2026年6月'）不作为 query 关键词——"
        "统一转为 time_filter（仅左端点，如 '>2026-06-01'）。query 只保留实体、渠道、指标词。\n"
        "  · 文档/策略证据用 concept；结构化数据（数字、明细、表格）用 structured。\n"
        "  · time_filter 按共享规则中的【高级时间推断】设置（仅左端点），不涉及时效则留空。"
    ),
    "bailian": (
        "【Bailian 专属规则】\n"
        "  · 格式：完整语义化句子/问句，含充分上下文\n"
        "  · 国家缩写必须展开三元组：UK → \"UK/英国/United Kingdom\"\n"
        "  · tags（⚠️ 百炼 tag 过滤「同 dimension 不能冲突」）：\n"
        "    - L2 业务 tag 最多 1 个，从 BAILIAN_L2_TAGS 枚举挑最贴切\n"
        "    - 时效 tag 最多 1 个（YYYY_QN 或背景类 \"last_long\"）\n"
        "    - 总数 ≤ 2 且两类各 ≤ 1；没把握宁可不加（召回优先）\n"
        "  · indexes：从 allowed_indexes 中按 focus 业务实体选\n"
        "  · time_filter 恒为 null（时效走 tags）\n"
        "  · 示例：\"nubia UK/英国/United Kingdom 2026年3月份 DTC 销售额和GMV表现\""
        " tags=[\"DTC\",\"2026_Q1\"]"
    ),
    "notion": (
        "【Notion 专属规则】\n"
        "  · 自然语言检索，关键词或短句均可\n"
        "  · 不输出源专属过滤字段；仅提供 query 与适用的时间/作者条件"
    ),
    "google": (
        "【Google Workspace 专属规则】\n"
        "0. 【数量预算（少而精硬纪律）】\n"
        "   · focuses=1：默认 1 条；只有 focus 含两个明显异质切面（如 数据+概念）才 2 条\n"
        "   · focuses≥2：累计 ≤ 2 条；优先覆盖信息密度更高的 focus，其余 focus\n"
        "     让飞书/百炼承担\n"
        "   · 单条 keywords 2-4 个；>4 个会导致 Drive 全文 AND 崩塌\n"
        "   · 禁止把多个竞品/多个产品型号/多个地区塞入一条\n"
        "1. 默认：google_enable_fulltext=true、google_order_by=null（最深全文匹配）\n"
        "2. 用户找近期修改/阅读的文件：google_enable_fulltext=false（标题检索）\n"
        "   并 google_order_by='viewedByMeTime desc' 或 'modifiedTime desc'\n"
        "3. `query` 必须是 2-4 个核心名词，空格分隔（例：'REDMAGIC 营销 上市'），\n"
        "   底层自动做同义词泛化，绝对不输出长句\n"
        "4. 【按 query_type 适配 query 写法】\n"
        "   · structured（数据类）：query 含「品牌+渠道/国家+指标词」（例 'nubia UK DTC GMV'）；\n"
        "     强烈建议加 time_filter；月份中英文系统自动 OR，只写一种写法\n"
        "   · concept：2-3 个关键词（例 'REDMAGIC 竞品 分析'），不加 time_filter 扩召回\n"
        "5. 【社媒/营销 query】底层自动 OR '营销/marketing/campaign/推广/投放/广告'\n"
        "   LLM 只需输出一种主关键词；显式枚举平台名只在用户问'各平台对比'时\n"
        "   常规简洁写 'nubia 社媒 营销 预算'（系统会扩到 KOL/Affiliate/Paid media）"
    ),
}


# 哪些源需要在 plan_only/global_planner 拼共享 COMMON + QUERY_TYPE 段
_SOURCES_USING_COMMON_HINTS = {"feishu", "google"}


def get_source_query_rules(source_name: str, include_common: bool = True) -> str:
    """获取格式化后的 source query rules（按需追加共享段）。

    Args:
        source_name: 源名（feishu / bailian / google / notion）
        include_common: True（默认）追加 COMMON_SUB_QUERY_HINTS + QUERY_TYPE_DESCRIPTION
            到 per-source 规则尾部——给 SourceAgent v1 plan_only LLM 用，让单源 prompt
            自包含。global_planner 在 prompt 顶部统一注入这两段后传 False 取纯特有部分。
    """
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d")
    template = SOURCE_QUERY_RULES.get(source_name, "")
    body = template.format(now_str=now_str) if "{now_str}" in template else template
    if include_common and source_name in _SOURCES_USING_COMMON_HINTS:
        body = (
            body + "\n\n" + COMMON_SUB_QUERY_HINTS.format(now_str=now_str)
            + "\n\n" + QUERY_TYPE_DESCRIPTION
        )
    return body


# 国家/地区缩写展开规则（bailian 专用）
COUNTRY_ABBREV_RULE = (
    "国家/地区缩写展开规则（仅适用于 bailian sub_query，不约束其他源的英文术语）：\n"
    "  query 中出现国家缩写时，bailian sub_query 里必须同时写出缩写+中文全称+英文全称。\n"
    "  常用对照：\n"
    "    UK/英国/United Kingdom、EU/欧洲/Europe、US/美国/United States、\n"
    "    SG/新加坡/Singapore、MX/墨西哥/Mexico、SA/沙特/Saudi Arabia、\n"
    "    UAE/阿联酋/United Arab Emirates、KR/韩国/South Korea、BR/巴西/Brazil、\n"
    "    GL/全球/Global、CA/加拿大/Canada、AU/澳大利亚/Australia、\n"
    "    JP/日本/Japan、DE/德国/Germany、FR/法国/France、IT/意大利/Italy"
)
