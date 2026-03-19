from __future__ import annotations

import asyncio
import json
import sys
import traceback
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from src.config import get_settings
from src.schemas import HealthResponse, JobCreateResponse, JobEventsResponse, JobResumeResponse, JobStateResponse, PlatformType
from src.services.event_bus import EventBus
from src.workflow.graph import WorkflowEngine
from src.workflow.state import AgentState, state_snapshot


def _force_windows_proactor_policy() -> None:
    if sys.platform != "win32":
        return
    policy_cls = getattr(asyncio, "WindowsProactorEventLoopPolicy", None)
    if policy_cls is None:
        return
    current = asyncio.get_event_loop_policy()
    if not isinstance(current, policy_cls):
        asyncio.set_event_loop_policy(policy_cls())


def _loop_supports_async_subprocess() -> bool:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return True

    if sys.platform == "win32":
        selector_cls = getattr(asyncio, "SelectorEventLoop", None)
        if selector_cls is not None and isinstance(loop, selector_cls):
            return False
        if "SelectorEventLoop" in type(loop).__name__:
            return False
    return True


_force_windows_proactor_policy()
settings = get_settings()
event_bus = EventBus()
workflow_engine = WorkflowEngine(settings=settings, event_bus=event_bus)

@asynccontextmanager
async def lifespan(app: FastAPI):
    _force_windows_proactor_policy()
    loop = asyncio.get_running_loop()
    app.state.loop_policy = type(asyncio.get_event_loop_policy()).__name__
    app.state.loop_type = type(loop).__name__
    app.state.subprocess_supported = _loop_supports_async_subprocess()
    yield


app = FastAPI(title="Autonomous Content Agent API", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def _startup_force_proactor() -> None:
    _force_windows_proactor_policy()
    app.state.subprocess_supported = _loop_supports_async_subprocess()


@app.middleware("http")
async def catch_unhandled_exceptions(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        return JSONResponse(
            status_code=500,
            content={
                "error": str(exc),
                "traceback": traceback.format_exc(),
            },
        )


def _sse_message(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


async def _save_upload_files(job_id: str, files: list[UploadFile]) -> list[str]:
    job_dir = settings.upload_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: list[str] = []
    for file in files:
        safe_name = Path(file.filename or "unnamed.jpg").name
        target_path = job_dir / safe_name
        content = await file.read()
        target_path.write_bytes(content)
        saved_paths.append(str(target_path.resolve()))
    return saved_paths


async def _execute_job(initial_state: AgentState) -> None:
    job_id = initial_state["job_id"]
    try:
        result_state = await workflow_engine.run(initial_state)
        if result_state.get("error"):
            failed_node = event_bus.get_failed_node(job_id)
            await event_bus.mark_failed(job_id, result_state, failed_node=failed_node)
            await event_bus.publish(
                job_id,
                "JOB_FAILED",
                "任务执行失败",
                {
                    "state": state_snapshot(result_state),
                    "error": result_state.get("error"),
                    "failed_node": failed_node,
                },
            )
            return

        await event_bus.mark_completed(job_id, result_state)
        await event_bus.publish(
            job_id,
            "JOB_COMPLETED",
            "任务执行完成",
            {"state": state_snapshot(result_state)},
        )
    except Exception as exc:
        trace = traceback.format_exc()
        failed_state = dict(initial_state)
        failed_state["error"] = str(exc)
        failed_state["traceback"] = trace
        failed_node = event_bus.get_failed_node(job_id)
        await event_bus.mark_failed(job_id, failed_state, failed_node=failed_node)
        await event_bus.publish(
            job_id,
            "JOB_FAILED",
            "任务执行异常",
            {
                "state": state_snapshot(failed_state),
                "error": str(exc),
                "traceback": trace,
                "failed_node": failed_node,
            },
        )


@app.get("/api/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse()


@app.post("/api/jobs", response_model=JobCreateResponse)
async def create_job(
    platform: Annotated[PlatformType, Form(...)],
    user_requirement: Annotated[str, Form(...)],
    images: Annotated[list[UploadFile], File(...)],
    max_retries: Annotated[int | None, Form()] = None,
) -> JobCreateResponse:
    if not images:
        raise HTTPException(status_code=400, detail="至少上传一张图片")

    job_id = uuid4().hex
    image_paths = await _save_upload_files(job_id, images)
    initial_state: AgentState = {
        "job_id": job_id,
        "platform": platform,
        "user_requirement": user_requirement,
        "image_paths": image_paths,
        "retry_count": 0,
        "max_retries": max_retries if max_retries is not None else settings.default_max_retries,
        "review_passed": False,
        "browser_status": "",
    }
    event_bus.create_job(job_id, dict(initial_state))
    await event_bus.publish(job_id, "JOB_CREATED", "任务已创建，开始执行", {"state": state_snapshot(initial_state)})
    asyncio.create_task(_execute_job(initial_state))
    return JobCreateResponse(job_id=job_id)


@app.get("/api/jobs/{job_id}", response_model=JobStateResponse)
async def get_job(job_id: str) -> JobStateResponse:
    if not event_bus.has_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    state = event_bus.get_job_state(job_id)
    failed_node = ""
    if state["status"] == "failed":
        failed_node = event_bus.get_failed_node(job_id)
    return JobStateResponse(job_id=job_id, status=state["status"], state=state["state"], failed_node=failed_node)


NODE_LABEL_TO_NAME = {"A": "node_a_vision", "B": "node_b_copy", "C": "node_c_review", "D": "node_d_browser", "E": "node_e_notify"}


@app.post("/api/jobs/{job_id}/resume", response_model=JobResumeResponse)
async def resume_job(job_id: str) -> JobResumeResponse:
    """Resume a failed job from the failed node instead of restarting from scratch."""
    if not event_bus.has_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")

    job_info = event_bus.get_job_state(job_id)
    if job_info["status"] != "failed":
        raise HTTPException(status_code=400, detail="只有失败的任务才能恢复执行")

    failed_node = event_bus.get_failed_node(job_id)
    if not failed_node or failed_node not in NODE_LABEL_TO_NAME:
        raise HTTPException(status_code=400, detail="无法确定失败节点，请重新创建任务")

    # Build resumed state from the old final_state
    old_state: dict = dict(job_info["state"])

    # Create a new job_id so SSE / events are clean
    new_job_id = uuid4().hex
    old_state["job_id"] = new_job_id
    old_state["error"] = ""
    old_state["resume_from_node"] = failed_node

    # Restore image_paths from original job events if missing in final_state snapshot
    if not old_state.get("image_paths"):
        for evt in event_bus.get_job_events(job_id):
            paths = evt.get("data", {}).get("state", {}).get("image_paths")
            if paths:
                old_state["image_paths"] = paths
                break

    event_bus.create_job(new_job_id, old_state)
    await event_bus.publish(
        new_job_id,
        "JOB_RESUMED",
        f"从节点 {failed_node} 恢复执行（原任务 {job_id}）",
        {"state": state_snapshot(old_state), "original_job_id": job_id, "resume_from_node": failed_node},
    )
    asyncio.create_task(_execute_job(old_state))
    return JobResumeResponse(job_id=new_job_id, original_job_id=job_id, resumed_from_node=failed_node)


@app.get("/api/jobs/{job_id}/events", response_model=JobEventsResponse)
async def get_job_events(
    job_id: str,
    order: Annotated[Literal["asc", "desc"], Query()] = "asc",
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
) -> JobEventsResponse:
    if not event_bus.has_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")

    state = event_bus.get_job_state(job_id)
    events = event_bus.get_job_events(job_id)
    if order == "desc":
        events = list(reversed(events))

    paged_events = events[offset : offset + limit]
    return JobEventsResponse(
        job_id=job_id,
        status=state["status"],
        total=len(events),
        events=paged_events,
    )


@app.get("/api/events/{job_id}")
async def events(job_id: str):
    if not event_bus.has_job(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    queue = event_bus.get_queue(job_id)

    async def event_stream():
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=15)
                yield _sse_message(event)
                if event["type"] in {"JOB_COMPLETED", "JOB_FAILED"}:
                    break
            except asyncio.TimeoutError:
                heartbeat = {
                    "type": "HEARTBEAT",
                    "job_id": job_id,
                    "message": "heartbeat",
                    "timestamp": "",
                    "data": {},
                }
                yield _sse_message(heartbeat)

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(event_stream(), media_type="text/event-stream", headers=headers)


@app.get("/events/{job_id}")
async def events_alias(job_id: str):
    return await events(job_id)
