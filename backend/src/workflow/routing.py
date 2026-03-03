from __future__ import annotations

from typing import Literal

from src.workflow.state import AgentState


def review_route(state: AgentState) -> Literal["to_browser", "to_rewrite", "to_notify"]:
    if state.get("review_passed"):
        return "to_browser"
    if int(state.get("retry_count", 0)) >= int(state.get("max_retries", 3)):
        return "to_notify"
    return "to_rewrite"
