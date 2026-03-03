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
        await self._emit(state, "NODE_START", "Node A 视觉分析开始", {"node": "A"})
        analysis = await self.models.analyze_images(state.get("image_paths", []))
        await self._emit(
            state,
            "NODE_LOG",
            "Node A 视觉分析完成",
            {"node": "A", "vision_analysis": analysis},
        )
        return {"vision_analysis": analysis}

    async def node_b_copy(self, state: AgentState) -> dict[str, Any]:
        await self._emit(
            state,
            "NODE_START",
            "Node B 爆款文案生成中",
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
            "Node B 文案已更新",
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
        await self._emit(state, "NODE_START", "Node C 主编审稿中", {"node": "C"})

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

        issues = list(dict.fromkeys([*deterministic["issues"], *llm_review.get("issues", [])]))
        rewrite_instructions = list(
            dict.fromkeys(
                [
                    *deterministic["rewrite_instructions"],
                    *llm_review.get("rewrite_instructions", []),
                ]
            )
        )
        passed = bool(deterministic["passed"] and llm_review.get("passed", False) and len(issues) == 0)

        if passed:
            await self._emit(
                state,
                "REVIEW_PASSED",
                "Node C 审稿通过",
                {"node": "C", "metrics": {"zh_char_count": deterministic["zh_char_count"], "emoji_count": deterministic["emoji_count"]}},
            )
            return {"review_passed": True, "critique_feedback": ""}

        retry_count = int(state.get("retry_count", 0)) + 1
        critique_feedback = "；".join(rewrite_instructions) or "请提升可读性和吸引力，保持合规。"
        await self._emit(
            state,
            "REVIEW_FAILED",
            "Node C 审稿未通过，回到 Node B 重写",
            {
                "node": "C",
                "issues": issues,
                "rewrite_instructions": rewrite_instructions,
                "replacement_suggestions": deterministic["replacement_suggestions"],
                "retry_count": retry_count,
                "max_retries": state.get("max_retries", 3),
            },
        )
        return {
            "review_passed": False,
            "critique_feedback": critique_feedback,
            "retry_count": retry_count,
        }

    async def node_d_browser(self, state: AgentState) -> dict[str, Any]:
        await self._emit(state, "NODE_START", "Node D 浏览器操盘开始", {"node": "D"})
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
            "Node D 浏览器操盘完成",
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
                "达到最大重写次数，未进入浏览器发布阶段。"
                "请根据审稿意见调整需求后重新生成。"
            )
            await self._emit(
                state,
                "JOB_FAILED",
                "Node E 通知：审稿未通过且已达最大重试次数",
                {
                    "node": "E",
                    "human_instructions": note,
                    "critique_feedback": state.get("critique_feedback", ""),
                },
            )
            return {"browser_status": "skipped", "browser_note": note, "error": "review_retries_exhausted"}

        browser_status = state.get("browser_status", "")
        common_data = {
            "draft_title": state.get("draft_title", ""),
            "draft_content": state.get("draft_content", ""),
            "platform": state.get("platform", ""),
            "live_url": state.get("browser_live_url", ""),
            "human_instructions": state.get("browser_note", ""),
        }
        if browser_status == "ready":
            await self._emit(state, "BROWSER_READY", "浏览器已就绪，请人工确认发布", common_data)
        elif browser_status == "need_login":
            await self._emit(state, "BROWSER_NEED_LOGIN", "浏览器需要先登录创作中心", common_data)
        else:
            await self._emit(state, "BROWSER_FAILED", "浏览器操盘失败", common_data)
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
