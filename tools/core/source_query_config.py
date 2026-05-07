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


# ── 各源 Query 编写规则（供 SourceAgent 的 plan_only prompt 注入）──────────

SOURCE_QUERY_RULES: Dict[str, str] = {
    "feishu": (
        "【Feishu 专属规划指南】\n"
        "  · 格式：「品牌 + 国家(可选) + 核心动作词」，2-3 词，AND 精确匹配\n"
        "  · 长度：严格控制 5-15 字，过长会导致零召回\n"
        "  · 禁止：不包含具体月份（「12月」「Q1」），不包含过专业词（「邮件」「线下」「Google Ads」）\n"
        "  · 国家：必须用中文（美国/英国/德国/日本/墨西哥），禁止 US/UK/DE/JP/MX\n"
        "  · 动作词：从 [销售, 运营, 策略, KOL, 推广, 发布, 计划, 表现, 营销, 合作, 进度, 市场, 产品, 定价] 中选择\n"
        "  · 优先策略：先「品牌 + 动作词」（2词），必要时加国家（3词）\n"
        "  · tags 始终为 []、indexes 始终为 []\n"
        "  · time_filter：支持 \">YYYY-MM-DD\" 的格式或者相对时间词（如 \"last_30_days\"）。\n"
        "  · 【高级时间推断】当前系统时间是：{now_str}。如果用户寻求特定年份的文件（如'2026年的规划'），因为规划往往在上一年 Q3/Q4 开始写，你需要聪明地将 time_filter 设置为提前的时间点，例如 '>2025-09-01'。如果是某年终总结，可设置 '>2025-11-01'。如果不涉及时效，留空即可。\n"
        "  · 【身份归属推断】如果用户的 Query 中明确带有“我写的”、“我创建的”、“我的”、“我最近的工作”等归属要求，请将 `author_filter` 设置为 'me'。\n"
        "  · query_type（必填，影响底层路由和极速查询）：\n"
        "      - \"structured\"：focus 问的是结构化、可核对、可追踪的事实载体\n"
        "                     包括：指标/数字/排名/销量/GMV/转化率/库存，以及流程/项目管理/进度/\n"
        "                     owner/里程碑/任务状态/执行记录/排期/checklist\n"
        "                   → 将开启 Table API 深度解析表单和多标签页结构\n"
        "      - \"concept\" ：focus 问的是策略/原因/背景/定位/市场分析/方法论等文本理解\n"
        "                   → concept-only 文档按预算走常规文档解析\n"
        "      - \"personal_recent\"：focus 问的是极其空泛、与个人动作高度相关的近期查询\n"
        "                     例如：「我最近做了啥」「我最近写的文档」「我的近期规划」\n"
        "                     一旦判定为这类意图，必须使用此类型，并同时将 `author_filter` 设为 \"me\"。\n"
        "                     系统将启动专属极速纯 MCP 检索通道，实现秒级回复。\n"
        "  · 如果同一 focus 既需要结构化事实又需要背景解释，必须拆成两条 sub_query；禁止输出混合类型\n"
        "  · 正确示例：「nubia 销售」「hypershell 策略」「nubia 日本 运营」"
    ),
    "bailian": (
        "bailian query 规则：\n"
        "  · 格式：完整语义化句子/问句，含充分上下文\n"
        "  · 国家缩写必须展开三元组：UK → \"UK/英国/United Kingdom\"（缩写+中文+英文同时出现）\n"
        "  · tags 规则（⚠️ 百炼 tag 过滤约束「同 dimension 不能冲突」）：\n"
        "    - L2 业务 tag 最多 1 个（精准优先，从 BAILIAN_L2_TAGS 枚举中挑最贴切的那个）\n"
        "    - 时效 tag 最多 1 个（格式 YYYY_QN，或背景/规范类用 \"last_long\"）\n"
        "    - 一条 sub_query 的 tags 总数 ≤ 2 且两类各 ≤ 1\n"
        "    - 没把握的 L2 宁可不加 tag（召回优先），避免 AND 过滤零返回\n"
        "  · indexes：从 planner 给的 allowed_indexes 中按 focus 业务实体选择\n"
        "  · time_filter 始终为 null（时效走 tags）\n"
        "  · 示例：\"nubia UK/英国/United Kingdom 2026年3月份 DTC 销售额和GMV表现\" tags=[\"DTC\",\"2026_Q1\"]"
    ),
    "notion": (
        "notion query 规则：\n"
        "  · 自然语言检索，关键词或短句均可\n"
        "  · tags 始终为 []、indexes 始终为 []、time_filter 始终为 null"
    ),
    "google": (
        "【Google Workspace 专属规划指南】\n"
        "Google 源支持强大的全文与标题检索切换以及高级过滤，你需要根据诉求做出精准调度：\n"
        "1. 默认情况，保持 `google_enable_fulltext = true` 且 `google_order_by = null`，以获取最深度的全文匹配。\n"
        "2. 如果用户寻找近期修改或阅读的文件，设置 `google_enable_fulltext = false`（退化为纯标题检索），"
        "并开启强制排序 `google_order_by = 'viewedByMeTime desc'` 或 `'modifiedTime desc'`。\n"
        "3. 【极其重要】你的 `query` 必须提取为 2 到 4 个最核心的名词或专有名词，必须用空格隔开（例如：'REDMAGIC 营销 上市'）。系统会在底层自动为你做同义词泛化，绝对不能输出长句。\n"
        "4. 【Query 类型适配】根据 focus 的 query_type 调整 subquery 策略：\n"
        "   - data 类型（问销量/GMV/订单/库存/转化率等结构化数据）：\n"
        "     * subquery 必须包含「品牌 + 渠道/国家 + 指标词」，如 'nubia UK DTC GMV'\n"
        "     * 强烈建议使用 `time_filter` 过滤时间范围（如 `'>2025-03-01'`）\n"
        "     * 月份/季度的中英文同义词由系统底层自动扩展，**只需输出一种写法即可**（如 'March' 或 '3月' 任选其一，系统会自动 OR 'March'/'3月'/'Mar'）\n"
        "     * 如果涉及多个国家，subquery 里只保留最核心的 1-2 个国家\n"
        "   - concept 类型（问策略/原因/背景/定位/市场分析等）：\n"
        "     * subquery 保持 2-3 个关键词，如 'REDMAGIC 竞品 分析'\n"
        "     * 不使用 time_filter，扩大召回范围\n"
        "   - personal_recent 类型：设置 `author_filter = 'me'` 并启用 `google_order_by = 'viewedByMeTime desc'`\n"
        "   - 【社媒/营销/广告类 query 提示】当 focus 涉及社交媒体、营销、投放、广告、传播效果时：\n"
        "     * 系统底层关键词扩展层会自动 OR '营销/marketing/campaign/推广/投放/广告' 等同义词，**LLM 只需输出一种主关键词**\n"
        "     * 如果用户明确询问'各平台'分布或'按平台对比'，subquery 才需要显式枚举平台名（如 'YouTube Instagram TikTok'）\n"
        "     * 否则保持简洁，例如 'nubia 社媒 营销 预算' 即可（系统会自动扩展到 KOL/Affiliate/Paid media）\n"
        "5. 【高级时间推断】当前系统时间是：{now_str}。如果用户寻求特定年份的文件（如'2026年的规划'），因为规划往往在上一年 Q3/Q4 开始写，你需要聪明地将 `time_filter` 设置为提前的时间点，例如 `'>2025-09-01'`。如果是某年终总结，可设置 `'>2025-11-01'`。如果不涉及时效，留空即可。\n"
        "6. 【身份归属推断】如果用户的 Query 中明确带有“我写的”、“我创建的”、“我的”、“我最近的工作”等归属要求，请将 `author_filter` 设置为 `'me'`。"
    ),
}

def get_source_query_rules(source_name: str) -> str:
    """动态获取格式化后的 source query rules。
    
    自动注入当前的时间等动态上下文变量供 LLM 高级推断使用。
    """
    import datetime
    now_str = datetime.datetime.now().strftime("%Y-%m-%d")
    template = SOURCE_QUERY_RULES.get(source_name, "")
    # 如果模板中存在 {now_str} 占位符，则进行格式化，否则原样返回
    if "{now_str}" in template:
        return template.format(now_str=now_str)
    return template

# 国家/地区缩写展开规则（bailian 专用）
COUNTRY_ABBREV_RULE = (
    "国家/地区缩写展开规则（bailian sub_query 专用，feishu 禁用英文缩写）：\n"
    "  query 中出现国家缩写时，bailian sub_query 里必须同时写出缩写+中文全称+英文全称。\n"
    "  常用对照：\n"
    "    UK/英国/United Kingdom、EU/欧洲/Europe、US/美国/United States、\n"
    "    SG/新加坡/Singapore、MX/墨西哥/Mexico、SA/沙特/Saudi Arabia、\n"
    "    UAE/阿联酋/United Arab Emirates、KR/韩国/South Korea、BR/巴西/Brazil、\n"
    "    GL/全球/Global、CA/加拿大/Canada、AU/澳大利亚/Australia、\n"
    "    JP/日本/Japan、DE/德国/Germany、FR/法国/France、IT/意大利/Italy"
)

