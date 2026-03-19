from __future__ import annotations

from typing import Any, Literal, TypedDict


BrowserStatus = Literal["ready", "need_login", "failed", "skipped", ""]


class VisionAnalysis(TypedDict, total=False):
    overview: str
    features: list[str]
    tone: str
    suggested_angle: str
    keywords: list[str]


class AgentState(TypedDict, total=False):
    job_id: str
    platform: Literal["douyin", "xhs"]
    user_requirement: str
    image_paths: list[str]
    vision_analysis: VisionAnalysis
    draft_title: str
    draft_content: str
    critique_feedback: str
    retry_count: int
    max_retries: int
    review_passed: bool
    browser_status: BrowserStatus
    browser_live_url: str
    browser_note: str
    error: str
    resume_from_node: str


def with_default_state(input_state: AgentState) -> AgentState:
    state = dict(input_state)
    state.setdefault("vision_analysis", {})
    state.setdefault("draft_title", "")
    state.setdefault("draft_content", "")
    state.setdefault("critique_feedback", "")
    state.setdefault("retry_count", 0)
    state.setdefault("max_retries", 3)
    state.setdefault("review_passed", False)
    state.setdefault("browser_status", "")
    state.setdefault("browser_live_url", "")
    state.setdefault("browser_note", "")
    state.setdefault("error", "")
    state.setdefault("resume_from_node", "")
    return state


def state_snapshot(state: AgentState) -> dict[str, Any]:
    return {
        "job_id": state.get("job_id"),
        "platform": state.get("platform"),
        "user_requirement": state.get("user_requirement"),
        "image_paths": state.get("image_paths", []),
        "image_count": len(state.get("image_paths", [])),
        "retry_count": state.get("retry_count"),
        "max_retries": state.get("max_retries"),
        "draft_title": state.get("draft_title"),
        "draft_content": state.get("draft_content"),
        "review_passed": state.get("review_passed"),
        "browser_status": state.get("browser_status"),
        "browser_live_url": state.get("browser_live_url"),
        "browser_note": state.get("browser_note"),
        "error": state.get("error"),
    }
