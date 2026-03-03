"use client";

import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { JobEventsResponse, StreamEvent } from "@/lib/types";

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

interface JobStateResponse {
  status: string;
  state: Record<string, unknown>;
}

function toRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== "object") {
    return null;
  }
  return value as Record<string, unknown>;
}

function toNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return value;
  }
  if (typeof value === "string") {
    const parsed = Number(value);
    if (Number.isFinite(parsed)) {
      return parsed;
    }
  }
  return null;
}

function eventNode(event: StreamEvent): string {
  const data = toRecord(event.data);
  const node = data?.node;
  return typeof node === "string" ? node : "-";
}

function eventRetryCount(event: StreamEvent): number | null {
  const data = toRecord(event.data);
  if (!data) {
    return null;
  }
  const direct = toNumber(data.retry_count);
  if (direct !== null) {
    return direct;
  }
  const state = toRecord(data.state);
  if (!state) {
    return null;
  }
  return toNumber(state.retry_count);
}

export default function ReplayPage() {
  const searchParams = useSearchParams();
  const loadedFromQueryRef = useRef(false);

  const [jobIdInput, setJobIdInput] = useState("");
  const [loadedJobId, setLoadedJobId] = useState("");
  const [jobStatus, setJobStatus] = useState("");
  const [events, setEvents] = useState<StreamEvent[]>([]);
  const [eventTotal, setEventTotal] = useState(0);
  const [finalState, setFinalState] = useState<Record<string, unknown> | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState("");
  const [lastLoadedAt, setLastLoadedAt] = useState("");

  const loadReplay = useCallback(async (jobId: string) => {
    const trimmed = jobId.trim();
    if (!trimmed) {
      setError("Please enter a job id.");
      return;
    }

    setIsLoading(true);
    setError("");

    try {
      const [eventsRes, stateRes] = await Promise.all([
        fetch(`${BACKEND_URL}/api/jobs/${trimmed}/events?order=asc&offset=0&limit=2000`),
        fetch(`${BACKEND_URL}/api/jobs/${trimmed}`),
      ]);

      if (!eventsRes.ok) {
        const text = await eventsRes.text();
        throw new Error(text || `Failed to load events (${eventsRes.status})`);
      }
      if (!stateRes.ok) {
        const text = await stateRes.text();
        throw new Error(text || `Failed to load job state (${stateRes.status})`);
      }

      const eventsPayload = (await eventsRes.json()) as JobEventsResponse;
      const statePayload = (await stateRes.json()) as JobStateResponse;
      setLoadedJobId(trimmed);
      setJobStatus(eventsPayload.status || statePayload.status || "");
      setEvents(eventsPayload.events ?? []);
      setEventTotal(eventsPayload.total ?? 0);
      setFinalState(toRecord(statePayload.state) ?? null);
      setLastLoadedAt(new Date().toLocaleTimeString());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load replay.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    const queryJobId = searchParams.get("job_id")?.trim() ?? "";
    if (!queryJobId || loadedFromQueryRef.current) {
      return;
    }
    loadedFromQueryRef.current = true;
    setJobIdInput(queryJobId);
    void loadReplay(queryJobId);
  }, [searchParams, loadReplay]);

  const metrics = useMemo(() => {
    const nodeStartCounts: Record<string, number> = { A: 0, B: 0, C: 0, D: 0, E: 0 };
    let reviewFailedCount = 0;
    let reviewPassedCount = 0;
    let maxRetry = 0;

    for (const event of events) {
      if (event.type === "NODE_START") {
        const node = eventNode(event);
        if (nodeStartCounts[node] !== undefined) {
          nodeStartCounts[node] += 1;
        }
      }
      if (event.type === "REVIEW_FAILED") {
        reviewFailedCount += 1;
      }
      if (event.type === "REVIEW_PASSED") {
        reviewPassedCount += 1;
      }
      const retry = eventRetryCount(event);
      if (retry !== null && retry > maxRetry) {
        maxRetry = retry;
      }
    }

    return {
      nodeStartCounts,
      reviewFailedCount,
      reviewPassedCount,
      maxRetry,
      rounds: nodeStartCounts.B,
    };
  }, [events]);

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await loadReplay(jobIdInput);
  };

  return (
    <main className="page">
      <h1 className="title">Run Replay</h1>
      <p className="subtitle">
        Load one run by <code>job_id</code> to inspect node-by-node events, payloads, and retry rounds.
      </p>
      <p className="hint">
        <Link href="/">Back to submit page</Link>
      </p>

      <section className="card">
        <form onSubmit={onSubmit} className="inline-form">
          <input
            type="text"
            value={jobIdInput}
            onChange={(e) => setJobIdInput(e.target.value)}
            placeholder="Paste job_id..."
            className="inline-input"
          />
          <button className="btn btn-primary" type="submit" disabled={isLoading}>
            {isLoading ? "Loading..." : "Load Replay"}
          </button>
          <button
            className="btn btn-ghost"
            type="button"
            onClick={() => void loadReplay(jobIdInput)}
            disabled={isLoading || !jobIdInput.trim()}
          >
            Refresh
          </button>
        </form>
        {loadedJobId ? <p className="hint">Job ID: {loadedJobId}</p> : null}
        {jobStatus ? <p className="hint">Status: {jobStatus}</p> : null}
        {lastLoadedAt ? <p className="hint">Last loaded: {lastLoadedAt}</p> : null}
        {error ? <p className="hint replay-error">{error}</p> : null}
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Run Summary</h3>
        <div className="metric-grid">
          <div className="metric-item">
            <div className="metric-label">Total events</div>
            <div className="metric-value">{eventTotal}</div>
          </div>
          <div className="metric-item">
            <div className="metric-label">Rewrite rounds (Node B runs)</div>
            <div className="metric-value">{metrics.rounds}</div>
          </div>
          <div className="metric-item">
            <div className="metric-label">Review failed times</div>
            <div className="metric-value">{metrics.reviewFailedCount}</div>
          </div>
          <div className="metric-item">
            <div className="metric-label">Max retry_count</div>
            <div className="metric-value">{metrics.maxRetry}</div>
          </div>
        </div>
        <p className="hint" style={{ marginTop: 10 }}>
          Node starts: A={metrics.nodeStartCounts.A}, B={metrics.nodeStartCounts.B}, C={metrics.nodeStartCounts.C},
          D={metrics.nodeStartCounts.D}, E={metrics.nodeStartCounts.E}; review passed={metrics.reviewPassedCount}.
        </p>
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Event Timeline</h3>
        <ul className="log-list">
          {events.map((item, idx) => {
            const node = eventNode(item);
            const retryCount = eventRetryCount(item);
            return (
              <li className="log-item" key={`${item.timestamp}-${idx}-${item.type}`}>
                <div>
                  <strong>{item.type}</strong>{" "}
                  <span className="hint">
                    {item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : "--:--:--"}
                  </span>
                </div>
                <div>{item.message}</div>
                <div className="hint">
                  Node: {node}
                  {retryCount !== null ? ` | retry_count: ${retryCount}` : ""}
                </div>
                <details>
                  <summary>payload</summary>
                  <pre className="json-block">{JSON.stringify(item.data, null, 2)}</pre>
                </details>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="card" style={{ marginTop: 16 }}>
        <h3 style={{ marginTop: 0 }}>Final State</h3>
        <pre className="json-block">{JSON.stringify(finalState ?? {}, null, 2)}</pre>
      </section>
    </main>
  );
}
