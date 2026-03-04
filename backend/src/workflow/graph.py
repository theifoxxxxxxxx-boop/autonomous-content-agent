from __future__ import annotations

from typing import Any

from langgraph.graph import END, StateGraph

from src.config import Settings
from src.review.rules import evaluate_deterministic_rules
from src.services.browser_operator import BrowserOperator
from src.services.event_bus import EventBus
from src.services.model_clients import ModelClients
from src.workflow.routing import review_route
from src.workflow.state import AgentState, state_snapshot, with_default_state


def _dedupe(items: list[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _is_subjective_title_issue(text: str) -> bool:
    value = (text or "").lower()
    title_keywords = ("标题", "title")
    subjective_keywords = ("吸引", "钩子", "抓人", "不够亮眼", "不够有力", "click")
    return any(key in value for key in title_keywords) and any(key in value for key in subjective_keywords)


class WorkflowEngine:
    def __init__(self, settings: Settings, event_bus: EventBus):
        self.settings = settings
        self.event_bus = event_bus
        self.models = ModelClients(settings=settings)
        self.browser = BrowserOperator(settings=settings)
        self.graph = self._build_graph()

    def _build_graph(self):
        graph = StateGraph(AgentState)
        graph.add_node("node_a_vision", self.node_a_vision)
        graph.add_node("node_b_copy", self.node_b_copy)
        graph.add_node("node_c_review", self.node_c_review)
        graph.add_node("node_d_browser", self.node_d_browser)
        graph.add_node("node_e_notify", self.node_e_notify)

        graph.set_entry_point("node_a_vision")
        graph.add_edge("node_a_vision", "node_b_copy")
        graph.add_edge("node_b_copy", "node_c_review")
        graph.add_conditional_edges(
            "node_c_review",
            review_route,
            {
                "to_browser": "node_d_browser",
                "to_rewrite": "node_b_copy",
                "to_notify": "node_e_notify",
            },
        )
        graph.add_edge("node_d_browser", "node_e_notify")
        graph.add_edge("node_e_notify", END)
        return graph.compile()

    async def run(self, input_state: AgentState) -> AgentState:
        state = with_default_state(input_state)
        return await self.graph.ainvoke(state)

    async def node_a_vision(self, state: AgentState) -> dict[str, Any]:
        await self._emit(state, "NODE_START", "Node A visual analysis started", {"node": "A"})
        analysis = await self.models.analyze_images(state.get("image_paths", []))
        await self._emit(
            state,
            "NODE_LOG",
            "Node A visual analysis completed",
            {"node": "A", "vision_analysis": analysis},
        )
        return {"vision_analysis": analysis}

    async def node_b_copy(self, state: AgentState) -> dict[str, Any]:
        await self._emit(
            state,
            "NODE_START",
            "Node B copywriting in progress",
            {"node": "B", "retry_count": state.get("retry_count", 0)},
        )
        generated = await self.models.generate_copy(
            platform=state["platform"],
            requirement=state["user_requirement"],
            vision_analysis=state.get("vision_analysis", {}),
            critique_feedback=state.get("critique_feedback", ""),
        )
        await self._emit(
            state,
            "DRAFT_UPDATED",
            "Node B draft updated",
            {
                "node": "B",
                "draft_title": generated["title"],
                "draft_content": generated["content"],
                "retry_count": state.get("retry_count", 0),
            },
        )
        return {
            "draft_title": generated["title"],
            "draft_content": generated["content"],
        }

    async def node_c_review(self, state: AgentState) -> dict[str, Any]:
        await self._emit(state, "NODE_START", "Node C editorial review in progress", {"node": "C"})

        deterministic = evaluate_deterministic_rules(
            platform=state["platform"],
            title=state.get("draft_title", ""),
            content=state.get("draft_content", ""),
        )
        llm_review = await self.models.llm_editorial_review(
            platform=state["platform"],
            requirement=state["user_requirement"],
            title=state.get("draft_title", ""),
            content=state.get("draft_content", ""),
            deterministic_issues=deterministic["issues"],
        )

        all_issues = _dedupe([*deterministic["issues"], *llm_review.get("issues", [])])
        all_rewrite_instructions = _dedupe(
            [
                *deterministic["rewrite_instructions"],
                *llm_review.get("rewrite_instructions", []),
            ]
        )

        subjective_title_issues = [item for item in all_issues if _is_subjective_title_issue(item)]
        blocking_issues = [item for item in all_issues if item not in subjective_title_issues]

        advisory_suggestions = _dedupe(
            [
                *deterministic.get("advisory_suggestions", []),
                *subjective_title_issues,
            ]
        )

        blocking_rewrite_instructions = [
            item
            for item in all_rewrite_instructions
            if not _is_subjective_title_issue(item)
        ]

        llm_effective_passed = bool(llm_review.get("passed", False))
        if not llm_effective_passed and blocking_issues == [] and subjective_title_issues:
            llm_effective_passed = True

        passed = bool(
            deterministic["passed"]
            and llm_effective_passed
            and len(blocking_issues) == 0
        )

        if passed:
            await self._emit(
                state,
                "REVIEW_PASSED",
                "Node C review passed",
                {
                    "node": "C",
                    "metrics": {
                        "zh_char_count": deterministic["zh_char_count"],
                        "emoji_count": deterministic["emoji_count"],
                    },
                    "advisory_suggestions": advisory_suggestions,
                },
            )
            return {"review_passed": True, "critique_feedback": ""}

        retry_count = int(state.get("retry_count", 0)) + 1
        max_retries = int(state.get("max_retries", 3))

        critique_feedback = "；".join(blocking_rewrite_instructions) or "请保持合规并提升可读性。"

        if retry_count >= max_retries:
            force_note = "审核未完全通过但强制输出当前最佳版本。"
            await self._emit(
                state,
                "REVIEW_FORCED_PASS",
                "Node C reached max retries; force output enabled",
                {
                    "node": "C",
                    "issues": blocking_issues,
                    "advisory_suggestions": advisory_suggestions,
                    "replacement_suggestions": deterministic["replacement_suggestions"],
                    "retry_count": retry_count,
                    "max_retries": max_retries,
                    "force_pass_note": force_note,
                },
            )
            return {
                "review_passed": True,
                "retry_count": retry_count,
                "critique_feedback": "",
                "browser_note": force_note,
            }

        await self._emit(
            state,
            "REVIEW_FAILED",
            "Node C review failed; back to Node B rewrite",
            {
                "node": "C",
                "issues": blocking_issues,
                "rewrite_instructions": blocking_rewrite_instructions,
                "advisory_suggestions": advisory_suggestions,
                "replacement_suggestions": deterministic["replacement_suggestions"],
                "retry_count": retry_count,
                "max_retries": max_retries,
            },
        )
        return {
            "review_passed": False,
            "critique_feedback": critique_feedback,
            "retry_count": retry_count,
        }

    async def node_d_browser(self, state: AgentState) -> dict[str, Any]:
        await self._emit(state, "NODE_START", "Node D browser automation started", {"node": "D"})
        browser_llm = None
        if self.settings.browser_use_enabled:
            try:
                browser_llm = self.models.build_browser_llm()
            except Exception:
                browser_llm = None
        browser_result = await self.browser.run(
            platform=state["platform"],
            title=state.get("draft_title", ""),
            content=state.get("draft_content", ""),
            image_paths=state.get("image_paths", []),
            browser_llm=browser_llm,
        )
        await self._emit(
            state,
            "NODE_LOG",
            "Node D browser automation completed",
            {
                "node": "D",
                "browser_status": browser_result.status,
                "browser_live_url": browser_result.live_url,
                "browser_note": browser_result.note,
            },
        )
        return {
            "browser_status": browser_result.status,
            "browser_live_url": browser_result.live_url,
            "browser_note": browser_result.note,
        }

    async def node_e_notify(self, state: AgentState) -> dict[str, Any]:
        if not state.get("review_passed", False):
            note = (
                "Maximum rewrites reached and review still not passed. "
                "Please adjust requirements and regenerate."
            )
            await self._emit(
                state,
                "JOB_FAILED",
                "Node E notify: review not passed and retries exhausted",
                {
                    "node": "E",
                    "human_instructions": note,
                    "critique_feedback": state.get("critique_feedback", ""),
                },
            )
            return {"browser_status": "skipped", "browser_note": note, "error": "review_retries_exhausted"}

        common_data = {
            "draft_title": state.get("draft_title", ""),
            "draft_content": state.get("draft_content", ""),
            "platform": state.get("platform", ""),
            "live_url": state.get("browser_live_url", ""),
            "human_instructions": state.get("browser_note", ""),
        }
        browser_status = state.get("browser_status", "")
        if browser_status == "ready":
            await self._emit(state, "BROWSER_READY", "Browser ready, please confirm publish manually", common_data)
        elif browser_status == "need_login":
            await self._emit(state, "BROWSER_NEED_LOGIN", "Browser needs login before publishing", common_data)
        else:
            await self._emit(state, "BROWSER_FAILED", "Browser automation failed", common_data)
            return {"error": state.get("error", "") or "browser_failed"}
        return {}

    async def _emit(
        self,
        state: AgentState,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload = data or {}
        payload.setdefault("state", state_snapshot(state))
        await self.event_bus.publish(state["job_id"], event_type, message, payload)
