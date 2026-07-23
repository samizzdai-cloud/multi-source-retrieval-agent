"""Shared LLM client — litellm wrapper for DeepSeek models.

Used by: global_planner.py, query_planner.py, answer_agent.py
"""

from __future__ import annotations

import json
import logging
import os
import re
import time as _time
from typing import Any, Callable, Mapping, Optional

from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# ── LLM constants ────────────────────────────────────────────────
LLM_MODEL       = os.getenv("LLM_MODEL", "deepseek/deepseek-v4-flash")
LLM_MAX_TOKENS  = 8192
LLM_TEMPERATURE = 0.0

_RETRYABLE_KEYWORDS = ("503", "429", "UNAVAILABLE", "Resource exhausted",
                        "overloaded", "quota", "rate")


def configure_llm_client(
    *,
    api_key: str = "",
    model: str | None = None,
    fallback_model: str | None = None,
) -> None:
    """显式注入 llm_client 运行时配置（兼容旧签名，DeepSeek 场景 api_key 可忽略）。"""
    global LLM_MODEL
    if api_key:
        os.environ.setdefault("DEEPSEEK_API_KEY", api_key)
    if model:
        LLM_MODEL = model


# ── Trace hook ───────────────────────────────────────────────────
_llm_trace_hook: Any = None


def set_llm_trace_hook(hook) -> None:
    global _llm_trace_hook
    _llm_trace_hook = hook


def clear_llm_trace_hook() -> None:
    global _llm_trace_hook
    _llm_trace_hook = None


# ── LLM call ─────────────────────────────────────────────────────

def call_llm(
    prompt: str,
    max_tokens: int = LLM_MAX_TOKENS,
    timeout: float = 30.0,
    _caller_tag: str = "unknown",
    max_retries: int = 3,
    retry_delay: float = 1.5,
    model: str | None = None,
    thinking_budget: int | None = None,       # 兼容旧签名，DeepSeek 忽略
    thinking_config: dict | None = None,
    response_mime_type: str | None = None,     # 兼容旧签名，DeepSeek 忽略
    response_schema: dict | None = None,
    trace_callback: Callable[[Mapping[str, Any]], None] | None = None,
) -> str:
    """LLM call via litellm (DeepSeek). Returns response text.

    自动重试（指数退避）。

    Args:
        thinking_config: DeepSeek thinking 配置，
          如 {"thinking": {"type": "enabled"}, "output_config": {"effort": "high"}}。
        response_schema: 如需结构化输出，转为 prompt 内嵌 JSON 指令 + json_object。
    """
    import litellm

    use_model = model or LLM_MODEL
    # 保持 provider 前缀 (如 "deepseek/xxx") — litellm 需要它来识别 provider

    # response_schema → prompt 内嵌 + json_object
    messages = [{"role": "user", "content": prompt}]
    completion_kwargs: dict = {
        "model": use_model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": LLM_TEMPERATURE,
        "timeout": timeout,
    }

    if response_schema:
        schema_json = json.dumps(response_schema, ensure_ascii=False)
        messages[0]["content"] = (
            prompt + "\n\nOutput strictly as JSON matching this schema:\n" + schema_json
        )
        completion_kwargs["response_format"] = {"type": "json_object"}

    if thinking_config:
        completion_kwargs.update(thinking_config)

    def _emit_trace(payload: Mapping[str, Any]) -> None:
        """Emit body-free per-attempt telemetry without affecting the call path."""
        if trace_callback is None:
            return
        try:
            trace_callback(payload)
        except Exception:
            # Observability must never turn a recoverable provider failure into
            # a retrieval failure.
            pass

    last_exc: Exception | None = None
    for attempt in range(max_retries):
        started = _time.perf_counter()
        response_meta: dict[str, Any] = {}
        try:
            response = litellm.completion(**completion_kwargs)
            choice = response.choices[0]
            message = choice.message
            response_meta = {
                "finish_reason": str(getattr(choice, "finish_reason", "") or ""),
                "has_reasoning_content": bool(getattr(message, "reasoning_content", None)),
                "has_tool_calls": bool(getattr(message, "tool_calls", None)),
            }
            # Per DeepSeek's JSON-mode contract, only ``content`` is the final
            # answer. ``reasoning_content`` is a thinking trace, never a JSON
            # fallback. Empty content is retried because the API documents it
            # as an intermittent JSON-mode condition.
            text = message.content or ""
            if not text:
                raise RuntimeError("empty response from model")
            _emit_trace({
                "event": "llm_call_attempt", "caller_tag": _caller_tag,
                "model": use_model, "attempt": attempt + 1,
                "outcome": "success", "timeout_s": timeout,
                "max_tokens": max_tokens,
                "elapsed_ms": round((_time.perf_counter() - started) * 1000.0, 1),
                **response_meta,
            })
            if _llm_trace_hook is not None:
                try:
                    _llm_trace_hook(prompt, text, _caller_tag)
                except Exception:
                    pass
            return text
        except Exception as e:
            last_exc = e
            err_str = str(e)
            empty_response = "empty response from model" in err_str.lower()
            retryable = (
                empty_response
                or any(k.lower() in err_str.lower() for k in _RETRYABLE_KEYWORDS)
            )
            will_retry = retryable and attempt < max_retries - 1
            wait = retry_delay * (2 ** attempt) if will_retry else 0.0
            _emit_trace({
                "event": "llm_call_attempt", "caller_tag": _caller_tag,
                "model": use_model, "attempt": attempt + 1,
                "outcome": "retry" if will_retry else "failed",
                "error_type": "empty_response" if empty_response else type(e).__name__,
                "empty_response": empty_response, "timeout_s": timeout,
                "max_tokens": max_tokens,
                "elapsed_ms": round((_time.perf_counter() - started) * 1000.0, 1),
                "retry_delay_s": wait,
                **response_meta,
            })
            if will_retry:
                logger.warning(
                    "call_llm [%s] attempt %d/%d failed: %s — retrying in %.1fs",
                    _caller_tag, attempt + 1, max_retries, err_str[:80], wait,
                )
                _time.sleep(wait)
            else:
                break

    if _llm_trace_hook is not None:
        try:
            _llm_trace_hook(prompt, "", _caller_tag)
        except Exception:
            pass
    raise RuntimeError(
        f"LLM call failed after {max_retries} attempts [{_caller_tag}]: {last_exc}"
    ) from last_exc


# ── JSON helpers ──────────────────────────────────────────────────

def _fix_json(text: str) -> str:
    """Best-effort fix for common LLM JSON quirks (trailing commas, truncation)."""
    text = re.sub(r',\s*([}\]])', r'\1', text)
    return text


def _repair_truncated_json(text: str) -> str:
    """Try to close a JSON object/array truncated by max_tokens."""
    text = re.sub(r',?\s*"[^"]*$', '', text)
    text = re.sub(r',?\s*$', '', text)
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
    clean = text.strip().lstrip("\ufeff")
    if "```" in clean:
        clean = re.sub(r'```(?:json)?\s*', '', clean)
        clean = re.sub(r'```', '', clean)
        clean = clean.strip()
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        pass
    try:
        return json.loads(_fix_json(clean))
    except json.JSONDecodeError:
        pass
    # Structured providers can append a usage marker or a second text segment
    # after a valid object.  raw_decode accepts the first complete JSON value
    # instead of letting a greedy {.*} extraction swallow that tail.
    decoder = json.JSONDecoder()
    for index, char in enumerate(clean):
        if char not in "[{":
            continue
        try:
            value, _end = decoder.raw_decode(clean[index:])
            return value
        except json.JSONDecodeError:
            continue
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
    repaired = _repair_truncated_json(_fix_json(clean))
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass
    raise json.JSONDecodeError("No JSON found in response", clean, 0)
