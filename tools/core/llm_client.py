"""Shared LLM client — generic Gemini call wrappers.

Used by: tools/query_planner.py, agents/sub_agents/*.py, answer_agent.py
"""

import json
import logging
import os
import re
from typing import Any

from google import genai
from google.genai import types as genai_types
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── LLM constants ────────────────────────────────────────────────
# [P2.7] 从 .env 读取模型配置，支持 lite 模型 + fallback
LLM_MODEL       = os.getenv("GEMINI_MODEL", "gemini-3-flash-preview")
LLM_FALLBACK    = os.getenv("GEMINI_FALLBACK_MODEL", "")
LLM_MAX_TOKENS  = 8192
LLM_TEMPERATURE = 0.0

_client = genai.Client(api_key=os.getenv("GOOGLE_API_KEY", ""))

# ── Trace hook ───────────────────────────────────────────────────
_llm_trace_hook: Any = None


def set_llm_trace_hook(hook) -> None:
    """Register hook(prompt, response, caller_tag) called after every LLM call."""
    global _llm_trace_hook
    _llm_trace_hook = hook


def clear_llm_trace_hook() -> None:
    global _llm_trace_hook
    _llm_trace_hook = None


# ── Generic LLM call ─────────────────────────────────────────────

_RETRYABLE_KEYWORDS = ("503", "429", "UNAVAILABLE", "Resource exhausted", "overloaded", "quota")


def call_llm(prompt: str, max_tokens: int = LLM_MAX_TOKENS, timeout: float = 30.0,
             _caller_tag: str = "unknown",
             max_retries: int = 3, retry_delay: float = 1.5,
             model: str | None = None,
             thinking_budget: int | None = None) -> str:
    """Generic LLM call via Gemini API. Returns response text.

    自动重试（指数退避）：503 / 429 / UNAVAILABLE / quota exceeded → 最多 max_retries 次。

    Args:
        thinking_budget: None=use default (0 for flash, auto for pro-thinking models).
                        0=disable thinking. >0=explicit budget.
    Raises RuntimeError on API failure after retries.
    """
    import time as _time

    use_model = model or LLM_MODEL
    # Models with "-pro-preview" or "thinking" in name require thinking mode
    _needs_thinking = any(k in use_model for k in ("pro-preview", "thinking"))
    if thinking_budget is not None:
        _tb = thinking_budget
    elif _needs_thinking:
        _tb = 8192  # default thinking budget for pro models
    else:
        _tb = 0     # disable thinking for flash models (fast path)

    response_text = ""
    last_exc: Exception | None = None

    for attempt in range(max_retries):
        try:
            response = _client.models.generate_content(
                model=use_model,
                contents=prompt,
                config=genai_types.GenerateContentConfig(
                    max_output_tokens=max_tokens,
                    temperature=LLM_TEMPERATURE,
                    thinking_config=genai_types.ThinkingConfig(thinking_budget=_tb),
                    http_options=genai_types.HttpOptions(timeout=int(timeout * 1000)),
                ),
            )
            response_text = response.text or ""
            if _llm_trace_hook is not None:
                try:
                    _llm_trace_hook(prompt, response_text, _caller_tag)
                except Exception:
                    pass
            return response_text
        except Exception as e:
            last_exc = e
            err_str = str(e)
            retryable = any(k.lower() in err_str.lower() for k in _RETRYABLE_KEYWORDS)
            if retryable and attempt < max_retries - 1:
                wait = retry_delay * (2 ** attempt)  # 1.5s → 3s → 6s
                logger.warning(
                    "call_llm [%s] attempt %d/%d failed: %s — retrying in %.1fs",
                    _caller_tag, attempt + 1, max_retries, err_str[:80], wait,
                )
                _time.sleep(wait)
            else:
                break

    # [P2.7] Fallback to secondary model if primary fails and fallback configured
    if LLM_FALLBACK and (model is None or model == LLM_MODEL) and LLM_FALLBACK != use_model:
        logger.warning(
            "call_llm [%s]: primary model %s failed, trying fallback %s",
            _caller_tag, use_model, LLM_FALLBACK,
        )
        try:
            return call_llm(prompt, max_tokens=max_tokens, timeout=timeout,
                            _caller_tag=_caller_tag, max_retries=2,
                            model=LLM_FALLBACK, thinking_budget=thinking_budget)
        except Exception as fallback_exc:
            logger.warning("call_llm [%s]: fallback also failed: %s", _caller_tag, fallback_exc)

    if _llm_trace_hook is not None:
        try:
            _llm_trace_hook(prompt, response_text, _caller_tag)
        except Exception:
            pass
    raise RuntimeError(
        f"LLM API call failed after {max_retries} attempts [{_caller_tag}]: {last_exc}"
    ) from last_exc


def _fix_json(text: str) -> str:
    """Best-effort fix for common LLM JSON quirks (trailing commas, truncation)."""
    # Remove trailing commas before } or ]
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return text


def _repair_truncated_json(text: str) -> str:
    """Try to close a JSON object/array truncated by max_tokens."""
    # Strip any trailing incomplete string value
    text = re.sub(r',?\s*"[^"]*$', '', text)  # dangling key or string value
    text = re.sub(r',?\s*$', '', text)
    # Count unmatched braces/brackets and close them
    opens = {'[': ']', '{': '}'}
    stack: list[str] = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in opens:
            stack.append(opens[ch])
        elif ch in (']', '}'):
            if stack and stack[-1] == ch:
                stack.pop()
    return text + ''.join(reversed(stack))


def parse_json_response(text: str) -> Any:
    """Safely parse JSON from LLM response — handles code fences and preamble."""
    clean = text.strip()
    if "```" in clean:
        clean = re.sub(r'```(?:json)?\s*', '', clean)
        clean = re.sub(r'```', '', clean)
        clean = clean.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    # Try after fixing common LLM JSON quirks
    try:
        return json.loads(_fix_json(clean))
    except json.JSONDecodeError:
        pass
    match = re.search(r'(\{[\s\S]*\}|\[[\s\S]*\])', clean)
    if match:
        candidate = match.group(1)
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            try:
                return json.loads(_fix_json(candidate))
            except json.JSONDecodeError:
                pass
    # Last resort: repair truncated JSON (max_tokens cut-off)
    repaired = _repair_truncated_json(_fix_json(clean))
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    raise json.JSONDecodeError("No JSON found in response", clean, 0)
