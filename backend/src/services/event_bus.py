from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class JobRuntime:
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "running"
    final_state: dict[str, Any] = field(default_factory=dict)
    failed_node: str = ""


class EventBus:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRuntime] = {}

    def create_job(self, job_id: str, initial_state: dict[str, Any]) -> None:
        runtime = JobRuntime()
        runtime.final_state = dict(initial_state)
        self._jobs[job_id] = runtime

    def has_job(self, job_id: str) -> bool:
        return job_id in self._jobs

    def get_queue(self, job_id: str) -> asyncio.Queue[dict[str, Any]]:
        if job_id not in self._jobs:
            raise KeyError(f"Job not found: {job_id}")
        return self._jobs[job_id].queue

    def get_job_state(self, job_id: str) -> dict[str, Any]:
        if job_id not in self._jobs:
            raise KeyError(f"Job not found: {job_id}")
        runtime = self._jobs[job_id]
        return {"status": runtime.status, "state": runtime.final_state}

    def get_job_events(self, job_id: str) -> list[dict[str, Any]]:
        if job_id not in self._jobs:
            raise KeyError(f"Job not found: {job_id}")
        return list(self._jobs[job_id].events)

    async def publish(
        self,
        job_id: str,
        event_type: str,
        message: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        if job_id not in self._jobs:
            return
        payload = {
            "type": event_type,
            "job_id": job_id,
            "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "data": data or {},
        }
        self._jobs[job_id].events.append(payload)
        await self._jobs[job_id].queue.put(payload)

    async def mark_completed(self, job_id: str, final_state: dict[str, Any]) -> None:
        if job_id not in self._jobs:
            return
        self._jobs[job_id].status = "completed"
        self._jobs[job_id].final_state = dict(final_state)

    def get_failed_node(self, job_id: str) -> str:
        """Extract the last NODE_START node name from events as the failed node."""
        if job_id not in self._jobs:
            return ""
        runtime = self._jobs[job_id]
        if runtime.failed_node:
            return runtime.failed_node
        for event in reversed(runtime.events):
            if event.get("type") == "NODE_START":
                return event.get("data", {}).get("node", "")
        return ""

    async def mark_failed(self, job_id: str, final_state: dict[str, Any], failed_node: str = "") -> None:
        if job_id not in self._jobs:
            return
        self._jobs[job_id].status = "failed"
        self._jobs[job_id].final_state = dict(final_state)
        self._jobs[job_id].failed_node = failed_node or self.get_failed_node(job_id)
