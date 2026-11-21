"""AnswerAgent — 读取 RetrievalAgent 的 chunks，生成中文自然语言回答。

session.state['retrieval_result'] : RetrievalResult JSON
session.state['retrieval_meta']   : 性能统计 JSON
"""
import logging
import time

from google.adk.agents.llm_agent import Agent
from google.adk.models.lite_llm import LiteLlm
from google.genai import types as genai_types

from tools.core.llm_contracts import DeepSeekFlashClient
from tools.core.models import DEFAULT_DEEPSEEK_MODEL, THINKING_FLASH
from tools.core.retrieval_progress import progress_sink


_ANSWER_STARTED_AT_KEY = "_answer_started_at"
logger = logging.getLogger(__name__)


def _mark_answer_started(callback_context):
    callback_context.state[_ANSWER_STARTED_AT_KEY] = time.monotonic()
    return None


def _emit_answer_timing(callback_context):
    started_at = callback_context.state.get(_ANSWER_STARTED_AT_KEY)
    if not started_at:
        return None
    elapsed_ms = int((time.monotonic() - float(started_at)) * 1000)
    if progress_sink.get() is not None:
        logger.info("TIMING answer elapsed_ms=%d", elapsed_ms)
        return None
    return genai_types.Content(
        role="model",
        parts=[genai_types.Part(text=f"[answer] done elapsed_ms={elapsed_ms}")],
    )

_INSTRUCTION = """你是一个企业咨询专家，根据已检索到的知识片段回答用户问题。

## 检索到的知识片段
{retrieval_result}

## 回答规范
1. 直接用自然语言回答，基于 chunks[].text 内容综合表达，优先使用 confidence_tier 为 critical 的片段
2. 关键信息后标注来源编号 [①][②]
3. 禁止原样输出 JSON
4. 若 chunks 为空或所有 chunks 的 confidence_tier 均为 "noise"：
   直接告知用户"未检索到与问题相关的知识片段"，并建议换一种问法或缩小问题范围，不要尝试回答
5. 若 query 涉及多个实体的对比，但检索结果仅覆盖其中部分实体：
   - 如实呈现已有数据，明确标注缺失方"未检索到相关数据，无法完成对比"
   - 禁止猜测或编造缺失方的数据
6. 若知识片段与问题无关，直接说明无法回答
7. 关注每个 chunk 的 focus_query 字段：若用户问题中的某个 focus 在所有 chunks 的 focus_query 中均未出现，说明该维度未检索到数据，回答中应明确标注该维度数据缺失
   若有证据的 Focus 仍缺少部分必需项，分别给出已支持的结论和具体缺口。用户要求各对象的最新情况时，分别标明可核实的业务数据周期；文档修改日期不能证明业务数据属于该周期。其他品牌的材料只能在明确适用时作为旁证。
8. 末尾固定输出以下区块：

[来源]
① 来源（Google/feishu/bailian）>文档名 > 章节路径
"""

answer_agent = Agent(
    model=LiteLlm(
        model=DEFAULT_DEEPSEEK_MODEL,
        llm_client=DeepSeekFlashClient(),
        **THINKING_FLASH.to_litellm_kwargs(),
    ),
    name="answer",
    description="基于已检索 chunks 生成中文自然语言回答",
    instruction=_INSTRUCTION,
    before_agent_callback=_mark_answer_started,
    after_agent_callback=_emit_answer_timing,
)
