"""Shared root-agent configuration for local tests and ADK Web.

Environment knobs:
    RAG_PRESET=lite|balanced|deep_dive
    RAG_ENABLED_SOURCES=google,feishu,bailian
    GOOGLE_CREDENTIALS_PATH=/abs/path/to/credentials.json
    GOOGLE_TOKEN_PATH=/abs/path/to/token.json
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
except Exception:  # pragma: no cover - python-dotenv may be absent in some runtimes
    load_dotenv = None

from retrieve_agent import build_retrieve_agent
from tools.core.models import GlobalPlannerConfig, RetrieveConfig, SourceAgentConfig, SourceConfig


PROJECT_ROOT = Path(__file__).resolve().parent


def load_project_env() -> None:
    """Load project .env before any source module reads environment variables."""
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)


def _csv_env(name: str, default: List[str]) -> List[str]:
    raw = (os.getenv(name, "") or "").strip()
    if not raw:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _positive_float_env(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "") or default)
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def build_root_retrieve_agent(name: str = "retrieve_v1"):
    """Build RetrieveAgent with the local three-source default."""
    load_project_env()

    preset = (os.getenv("RAG_PRESET", "balanced") or "balanced").strip()
    enabled_sources = _csv_env("RAG_ENABLED_SOURCES", ["google", "feishu", "bailian"])

    # Feishu document reads are often slower than the base balanced timeout.
    source_overrides = {
        "feishu": SourceAgentConfig(
            source_timeout_s=_positive_float_env("RAG_FEISHU_SOURCE_TIMEOUT_S", 45.0),
        ),
    }
    planner_config = GlobalPlannerConfig(
        timeout_s=_positive_float_env("RAG_GLOBAL_PLANNER_TIMEOUT_S", 15.0),
    )

    return build_retrieve_agent(
        config=RetrieveConfig(
            preset=preset,  # type: ignore[arg-type]
            enabled_sources=enabled_sources,
            source_configs=source_overrides,
            global_planner=planner_config,
        ),
        source_config=SourceConfig(
            google_credentials_path=os.getenv("GOOGLE_CREDENTIALS_PATH", ""),
            google_token_path=os.getenv("GOOGLE_TOKEN_PATH", ""),
            load_from_env=True,
        ),
        name=name,
    )
