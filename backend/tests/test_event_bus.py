from __future__ import annotations

import pytest

from src.services.event_bus import EventBus


@pytest.mark.asyncio
async def test_publish_records_event_history_and_queue():
    bus = EventBus()
    bus.create_job("job-1", {"job_id": "job-1"})

    await bus.publish("job-1", "NODE_START", "node started", {"node": "A"})

    events = bus.get_job_events("job-1")
    assert len(events) == 1
    assert events[0]["type"] == "NODE_START"
    assert events[0]["message"] == "node started"
    assert events[0]["data"]["node"] == "A"

    queued = await bus.get_queue("job-1").get()
    assert queued["type"] == "NODE_START"

    events.append({"type": "OTHER"})
    assert len(bus.get_job_events("job-1")) == 1
