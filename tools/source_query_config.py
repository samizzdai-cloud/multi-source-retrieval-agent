"""Source Query Config — QueryPlanner 的规则外化配置

职责：
  1. 各检索源的 query 规则（动态组装 prompt）
  2. 多实体查询策略（场景A对比 / 场景B差异化）
  3. LLM tag 输出纠错（fuzzy fix）
  4. Plan 结构校验 + 自动修复（零 LLM）
  5. session_context 预留
"""

import logging
import re
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from tools.models import QueryPlan

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
_TIME_TAG_RE = re.compile(r'^(last_long|\d{4}_Q[1-4])$')


# ── Tag Fuzzy Fix（LLM 常见输出错误 → 正确枚举值）───────────────────────────

_TAG_FUZZY_MAP: Dict[str, str] = {
    # 大小写 / 下划线 / 空格变体
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

    # 1. 精确命中
    if tag in _L2_SET:
        return tag

    # 2. 时效 tag 格式
    if _TIME_TAG_RE.match(tag):
        return tag

    # 3. 大小写修正
    for valid in BAILIAN_L2_TAGS:
        if tag.lower() == valid.lower():
            return valid

    # 4. Fuzzy 映射
    normalized = tag.lower().replace("_", " ").replace("-", " ").strip()
    if normalized in _TAG_FUZZY_MAP:
        return _TAG_FUZZY_MAP[normalized]

    # 5. 时效 tag 宽松匹配（如 "2026-Q1" → "2026_Q1"）
    relaxed = tag.replace("-", "_").replace(" ", "_")
    if _TIME_TAG_RE.match(relaxed):
        return relaxed

    logger.debug("fix_tag: cannot fix '%s', discarding", raw_tag)
    return None


# ── 各源 Query 编写规则（按源分类，只注入可用源的规则）─────────────────────

# 新增源时：在此 dict 中添加对应条目，query_planner.py 无需改动。
SOURCE_QUERY_RULES: Dict[str, str] = {
    "feishu": (
        "feishu query 规则：\n"
        "  · 格式：「品牌 + 国家(可选) + 核心动作词」，2-3 词，AND 精确匹配\n"
        "  · 长度：严格控制 5-15 字，过长会导致零召回\n"
        "  · 禁止：不包含具体月份（如「12月」「Q1」），不包含过于专业的词（如「邮件」「线下」「Google Ads」「渠道拓展」）\n"
        "  · 国家：必须用中文（美国/英国/德国/日本/墨西哥），禁止用 US/UK/DE/JP/MX\n"
        "  · 动作词：从 [销售, 运营, 策略, KOL, 推广, 发布, 计划, 表现, 营销, 合作, 进度, 市场, 产品, 定价] 中选择\n"
        "  · 优先策略：先用「品牌 + 动作词」（2词），如果有明确国家再加国家（3词）\n"
        "  · tags 始终为 []\n"
        "  · 正确示例：「nubia 销售」「hypershell 策略」「nubia 日本 运营」「hypershell 美国 营销」\n"
        "  · 错误示例（禁止）：「nubia 邮件 营销」「hypershell 线下 策略」「nubia Amazon 12月」（过于具体）"
    ),
    "bailian": (
        "bailian query 规则：\n"
        "  · 格式：完整语义化句子或问句，含充分上下文\n"
        "  · 国家缩写必须展开：UK → \"UK/英国/United Kingdom\"（同时写缩写+中文+英文）\n"
        "  · tags 填写：l2 业务分类最多 2 个（精准优先，OR 语义）；时效 tag 最多 1 个\n"
        "  · 背景/规范类 query 额外加 \"last_long\" 时效 tag\n"
        "  · 示例：\"nubia UK/英国/United Kingdom 2026年3月份DTC销售额和GMV表现\""
    ),
    "notion": (
        "notion query 规则：\n"
        "  · 自然语言检索，关键词或短句均可\n"
        "  · tags 始终为 []，indexes 始终为 []"
    ),
    "google": (
        "google query 规则：\n"
        "  · 自然语言搜索，可含完整句子或关键词组合\n"
        "  · tags 始终为 []，indexes 始终为 []"
    ),
}

# 国家/地区缩写展开规则（bailian 专用，供 prompt 注入）
COUNTRY_ABBREV_RULE = (
    "国家/地区缩写展开规则（仅用于 bailian sub_query，不用于 feishu）：\n"
    "  query 中出现国家缩写时，bailian sub_query 里必须同时写出缩写+中文全称+英文全称。\n"
    "  常用对照：\n"
    "    UK/英国/United Kingdom、EU/欧洲/Europe、US/美国/United States、\n"
    "    SG/新加坡/Singapore、MX/墨西哥/Mexico、SA/沙特/Saudi Arabia、\n"
    "    UAE/阿联酋/United Arab Emirates、KR/韩国/South Korea、BR/巴西/Brazil、\n"
    "    GL/全球/Global、CA/加拿大/Canada、AU/澳大利亚/Australia、\n"
    "    JP/日本/Japan、DE/德国/Germany、FR/法国/France、IT/意大利/Italy"
)

# ── 品牌智能路由配置 ─────────────────────────────────────────────────────────
# 根据 5-Query 深度分析结果：REDMAGIC 在飞书召回极差 (0 chunks)，仅走百炼
# 新增品牌时：在此 dict 添加对应条目（大小写由 get_brand_targets 统一处理）
BRAND_ROUTING: Dict[str, List[str]] = {
    "nubia": ["feishu", "bailian"],
    "REDMAGIC": ["bailian"],        # Feishu 召回差，仅走百炼
    "Hypershell": ["feishu", "bailian"],
    "Nothing": ["feishu", "bailian"],
}

# 品牌路由规则（注入 prompt）
BRAND_ROUTING_RULE = (
    "品牌智能路由规则：\n"
    "  · REDMAGIC 相关查询：仅生成 bailian sub_query，不生成 feishu（飞书召回率极低）\n"
    "  · nubia/Hypershell/Nothing：同时生成 feishu 和 bailian sub_query\n"
    "  · 识别品牌后自动应用路由，无需用户指定"
)


def get_brand_targets(query: str, default_targets: List[str]) -> List[str]:
    """根据 query 中的品牌名返回应使用的 targets 列表。"""
    query_lower = query.lower()
    for brand, targets in BRAND_ROUTING.items():
        if brand.lower() in query_lower:
            return targets
    return default_targets


def build_source_query_rules_text(available_sources: List[str]) -> str:
    """只注入当前可用源的 query 编写规则。未配置的源不出现在 prompt 中。"""
    lines = []
    for source in available_sources:
        rule = SOURCE_QUERY_RULES.get(source)
        if rule:
            lines.append(rule)
    if not lines:
        return ""
    text = "\n\n".join(lines)
    # 若 bailian 可用，追加国家缩写展开规则
    if "bailian" in available_sources:
        text += "\n\n" + COUNTRY_ABBREV_RULE
    # 追加品牌路由规则（feishu + bailian 同时可用时）
    if "feishu" in available_sources and "bailian" in available_sources:
        text += "\n\n" + BRAND_ROUTING_RULE
    return text


# ── 显式源指定词汇表（required_targets 检测）────────────────────────────────

# 用户在 query 中使用以下词汇时，LLM 应将该 focus_group 的 required_targets 设为对应源。
# 支持两类格式：
#   · 自然语言：「在飞书里找」「飞书里查一下」「用 notion 查」
#   · 标签格式：「#feishu」「#bailian」「#notion」（hash tag 风格，显式路由）
# 新增源时：在此 dict 添加对应条目，query_planner.py 无需修改。
SOURCE_EXPLICIT_INDICATORS: Dict[str, List[str]] = {
    "feishu":  ["#feishu", "飞书", "feishu", "飞书文档", "飞书wiki", "知识库（飞书）"],
    "bailian": ["#bailian", "#知识库", "百炼", "bailian", "知识库（百炼）"],
    "notion":  ["#notion", "notion"],
    "google":  ["#google", "#drive", "google drive", "google文档"],
}


def build_required_targets_rule_text(available_sources: List[str]) -> str:
    """生成 required_targets 检测规则文本，只包含可用源的触发词。

    新增源时只需更新 SOURCE_EXPLICIT_INDICATORS，此函数自动包含。
    """
    lines = []
    for source in available_sources:
        indicators = SOURCE_EXPLICIT_INDICATORS.get(source, [])
        if indicators:
            kws = "/".join(f'"{w}"' for w in indicators)
            lines.append(f"  {kws} → \"{source}\"")
    if not lines:
        return "  （无可用源）"
    return "\n".join(lines)


# ── 多实体查询策略 ───────────────────────────────────────────────────────────

_MULTI_ENTITY_STRATEGY = """⚠️ 多实体查询策略（query 涉及两个或以上实体时）：
每个实体单独一个 focus_group，focuses 只描述该实体的信息需求。

【场景 A — 纯对比查询】明确含"对比/比较/vs/哪个更/哪一个"等词：
  → 两侧 group 的 focuses 必须覆盖相同维度（对称）且时间约束对称
  → 示例："nubia vs hypershell 各渠道表现"

【场景 B — 多实体差异化查询】针对不同实体询问不同内容：
  → 每个 group 的 focuses 按该实体实际被问到的内容独立拆分，无需对称
  → 示例："Nubia 的 email 订阅情况 和 Hypershell 的 DTC 增长速度"
        group A: ["nubia email订阅用户数", "nubia邮件增长率"]
        group B: ["hypershell DTC渠道销售额", "hypershell DTC增长趋势"]"""

_COMPARISON_KEYWORDS = {"对比", "比较", "vs", "哪个更", "哪一个", "谁", "哪个"}


def build_multi_entity_strategy_text() -> str:
    return _MULTI_ENTITY_STRATEGY


# ── Plan 校验 + 自动修复 ─────────────────────────────────────────────────────

def validate_and_fix_plan(
    plan: "QueryPlan",
    available_sources: List[str],
    configured_indexes: List[str],
) -> "QueryPlan":
    """规则校验 + 自动修复，零 LLM 调用。

    Rules:
      1. Tag fuzzy fix：每个 tag 经过 fix_tag 修正
      2. Targets 校验：只保留 available_sources 中的值
      3. Indexes 校验：只保留 configured_indexes 中的值
      4. 去掉空 focus group
      5. 对比查询维度对称性检测（日志警告，不强改语义）
      6. 无实体 query（所有 indexes 为空）→ 正常通过，不报错
    """
    for fg in plan.focus_groups:
        for sq in fg.sub_queries:
            # Rule 1: tag fuzzy fix
            if sq.tags:
                fixed = [fix_tag(t) for t in sq.tags]
                sq.tags = [t for t in fixed if t is not None]

            # Rule 1b: [P1] l2 tag 硬截断 ≤2，时效 tag ≤1
            # prompt 软约束不可靠，LLM 可能输出 6 个 tag 导致 OR 语义过宽
            if sq.tags:
                l2_tags_sq = [t for t in sq.tags if not _TIME_TAG_RE.match(t)]
                time_tags_sq = [t for t in sq.tags if _TIME_TAG_RE.match(t)]
                sq.tags = l2_tags_sq[:2] + time_tags_sq[:1]

            # Rule 2: targets 校验
            sq.targets = [t for t in sq.targets if t in available_sources]
            if not sq.targets:
                sq.targets = list(available_sources)

            # Rule 3: indexes 校验
            sq.indexes = [i for i in sq.indexes if i in configured_indexes]

        # Rule 3b: [P2] required_targets 校验：只保留该 FG sub_queries.targets 中实际存在的源
        fg_all_targets = {t for sq in fg.sub_queries for t in sq.targets}
        if hasattr(fg, 'required_targets'):
            fg.required_targets = [t for t in fg.required_targets if t in fg_all_targets]

    # Rule 4: 去掉空 focus group
    plan.focus_groups = [
        fg for fg in plan.focus_groups
        if fg.focuses and fg.sub_queries
    ]

    # Rule 5: 对比查询维度对称性检测
    is_comparison = any(kw in plan.original_query for kw in _COMPARISON_KEYWORDS)
    if is_comparison and len(plan.focus_groups) >= 2:
        focus_counts = [len(fg.focuses) for fg in plan.focus_groups]
        if len(set(focus_counts)) > 1:
            logger.warning(
                "Plan validation: comparison query but focus counts unbalanced %s "
                "(expected symmetric). Query: %s",
                focus_counts, plan.original_query[:80],
            )

    return plan

# ── 路由规则（根据 query_type 决定 sub_query 分配）────────────────────────────

def build_routing_rules_text(available_sources: list[str], max_sub_queries: int) -> str:
    """生成数量与路由规则文本，根据可用源动态调整。"""
    # 基础规则
    lines = [
        f"- 每个 focus_group 最多生成 {max_sub_queries} 个 sub_queries，宁精勿多",
    ]

    # 只有同时有 feishu 和 bailian 时才需要路由规则
    if "feishu" in available_sources and "bailian" in available_sources:
        lines.append(
            "- 智能路由（根据 query_type 决定 sub_query 分配比例）：\n"
            "  · query_type=\"data\"：bailian 为主力（2-3个语义query），feishu 发 1 个精简关键词作为补充\n"
            "  · query_type=\"concept\"：feishu 和 bailian 同等重要，各 1-2 个\n"
            "  · query_type=\"mixed\"：两者都需要，data 维度多发 bailian，concept 维度多发 feishu"
        )

    return "\n".join(lines)


# ── Tags 规则（bailian 专用）───────────────────────────────────────────────────

_TAGS_RULES = """- tags 字段说明（仅对 bailian 有效，feishu/其他源的 sub_query 必须保持 tags: []）：
  - l2 业务分类：最多选 2 个（精准优先，过多 tag 导致 OR 语义过宽），多个时 OR 语义
  - 时效标签策略（重要，避免零返回）：
    · 默认不加时效 tag（扩大召回范围）
    · 仅在明确指定年份+季度时才加 YYYY_QN tag（如"2026年Q1"→加 "2026_Q1"）
    · 查询"去年/2025年"等历史数据时，不加时效 tag（历史数据可能未打 tag）
    · 背景/规范类 query（query_type=concept）：可加 "last_long"
  - ⚠️ 宁可不加 tag 也不要加错 tag 导致零返回"""


def build_tags_rules_text() -> str:
    """返回 tags 字段使用规则。"""
    return _TAGS_RULES