"""各检索源的"画像"——供 plan_only / plan_eval LLM 理解源能力后生成 sub_queries。

注意：这里**只存规划提示信息**（description / strengths / weaknesses / sub_query_hints），
不含 SDK 凭证、URL、index_id 等运行时配置（那些在 ``SourceConfig`` / env var 里）。

使用者：``plan_only_sub_agent`` / ``plan_eval_sub_agent``。
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class SourceProfile:
    """单一检索源的规划画像。"""
    source: str                              # "feishu" | "bailian" | "google" | "notion"
    description: str                         # 一句话总览
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    sub_query_hints: List[str] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        """渲染成 prompt 片段，供 LLM 模板拼接。"""
        def _bullets(items: List[str]) -> str:
            return "\n".join(f"  - {x}" for x in items) if items else "  - (none)"
        return (
            f"[source={self.source}]\n"
            f"description: {self.description}\n"
            f"strengths:\n{_bullets(self.strengths)}\n"
            f"weaknesses:\n{_bullets(self.weaknesses)}\n"
            f"sub_query_hints:\n{_bullets(self.sub_query_hints)}"
        )


SOURCE_PROFILES: Dict[str, SourceProfile] = {
    "feishu": SourceProfile(
        source="feishu",
        description="飞书云文档 / 多维表格 / 电子表格——内部 SOP、业务说明、运营数据",
        strengths=[
            "结构化多维表格（bitable）与电子表格（sheet）",
            "章节层级清晰的长文档（docx，含 summary）",
            "内部真实业务流程、组织制度、渠道数据",
        ],
        weaknesses=[
            "公开资讯 / 外部新闻覆盖弱",
            "实时数据（分钟级）覆盖弱",
        ],
        sub_query_hints=[
            "优先使用业务实体全称 + 维度词（如 'nubia DTC 渠道 销售数据'）",
            "涉及表格时加上字段名（'转化率 月度'）可提升召回",
        ],
    ),
    "bailian": SourceProfile(
        source="bailian",
        description="阿里云百炼知识库——经 rerank 的长文档片段，覆盖内部沉淀资料",
        strengths=[
            "qwen3-rerank 精排过，排序稳定",
            "跨文档语义检索，适合概念型 query",
            "tag 过滤支持（子业务领域 + 时效）",
        ],
        weaknesses=[
            "对极短 query / 纯关键字 query 精度不如 feishu",
            "不覆盖原始表格单元格粒度",
        ],
        sub_query_hints=[
            "使用自然语言句式（'xxx 是什么 / 如何做 xxx'）而非关键字堆叠",
            "必要时显式加时效限定（'最近半年'）",
        ],
    ),
    "google": SourceProfile(
        source="google",
        description="Google 搜索——公开互联网资讯、行业报告、竞品动态",
        strengths=[
            "实时性强（新闻 / 发布会）",
            "外部视角（行业分析 / 媒体评论）",
        ],
        weaknesses=[
            "无法访问内部私域资料",
            "结果嘈杂，需要 query 足够精确",
        ],
        sub_query_hints=[
            "使用英文专有名词可提升精度",
            "加时间 / 地区限定（'2025 China market'）",
        ],
    ),
    "notion": SourceProfile(
        source="notion",
        description="Notion 工作空间——产品规划文档、会议纪要、协作知识",
        strengths=[
            "半结构化规划文档",
            "跨项目的协作历史",
        ],
        weaknesses=[
            "结构化数据覆盖弱",
            "重复 / 草稿页面较多",
        ],
        sub_query_hints=[
            "带项目名或 owner 关键字（'<project> roadmap'）",
        ],
    ),
}


def get_profile(source: str) -> SourceProfile:
    """取指定源的 profile；缺失时抛 KeyError（显式，避免静默使用错误画像）。"""
    if source not in SOURCE_PROFILES:
        raise KeyError(
            f"Unknown source '{source}'. Known: {sorted(SOURCE_PROFILES)}"
        )
    return SOURCE_PROFILES[source]
