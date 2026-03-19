"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import type { Platform, StreamEvent } from "@/lib/types";

interface BrowserReadyState {
  status: "idle" | "ready" | "need_login" | "failed";
  liveUrl: string;
  instructions: string;
}

const BACKEND_URL = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:8000";

const NODE_LABELS: Record<string, string> = {
  A: "视觉分析 (Node A)",
  B: "文案生成 (Node B)",
  C: "审核校验 (Node C)",
  D: "浏览器自动化 (Node D)",
  E: "通知 (Node E)",
};

function getTimelineDotClass(type: string): string {
  if (type === "NODE_START") return "timeline-dot is-start";
  if (type === "JOB_FAILED" || type === "REVIEW_FAILED" || type === "BROWSER_FAILED")
    return "timeline-dot is-fail";
  if (
    type === "JOB_COMPLETED" ||
    type === "REVIEW_PASSED" ||
    type === "BROWSER_READY"
  )
    return "timeline-dot is-done";
  return "timeline-dot";
}

export default function HomePage() {
  const [platform, setPlatform] = useState<Platform>("xhs");
  const [requirement, setRequirement] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [jobId, setJobId] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [logs, setLogs] = useState<StreamEvent[]>([]);
  const [draftTitle, setDraftTitle] = useState("");
  const [draftContent, setDraftContent] = useState("");
  const [statusText, setStatusText] = useState("等待任务开始");
  const [jobFinalStatus, setJobFinalStatus] = useState("");
  const [failedNode, setFailedNode] = useState("");
  const [isResuming, setIsResuming] = useState(false);
  const [browserState, setBrowserState] = useState<BrowserReadyState>({
    status: "idle",
    liveUrl: "",
    instructions: "",
  });

  const fileInputRef = useRef<HTMLInputElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const isRunning = useMemo(
    () => isSubmitting || (jobId !== "" && jobFinalStatus === ""),
    [isSubmitting, jobId, jobFinalStatus],
  );

  const readyTag = useMemo(() => {
    if (browserState.status === "ready")
      return { cls: "tag tag-ok", text: "浏览器已就绪" };
    if (browserState.status === "need_login")
      return { cls: "tag tag-warn", text: "需要先登录" };
    if (browserState.status === "failed")
      return { cls: "tag tag-fail", text: "浏览器失败" };
    return null;
  }, [browserState.status]);

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

  /* ---- SSE ---- */
  const connectSse = (createdJobId: string) => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const source = new EventSource(`${BACKEND_URL}/api/events/${createdJobId}`);
    source.onmessage = (event: MessageEvent<string>) => {
      try {
        const payload = JSON.parse(event.data) as StreamEvent;
        setLogs((prev) => [payload, ...prev].slice(0, 250));
        setStatusText(payload.message);

        const data = payload.data ?? {};
        const title = data["draft_title"];
        const content = data["draft_content"];
        if (typeof title === "string" && title) setDraftTitle(title);
        if (typeof content === "string" && content) setDraftContent(content);

        if (payload.type === "BROWSER_READY") {
          setBrowserState({
            status: "ready",
            liveUrl: String(data["live_url"] ?? ""),
            instructions: String(data["human_instructions"] ?? ""),
          });
        } else if (payload.type === "BROWSER_NEED_LOGIN") {
          setBrowserState({
            status: "need_login",
            liveUrl: "",
            instructions: String(data["human_instructions"] ?? ""),
          });
        } else if (payload.type === "BROWSER_FAILED") {
          setBrowserState({
            status: "failed",
            liveUrl: "",
            instructions: String(data["human_instructions"] ?? ""),
          });
        }

        if (payload.type === "JOB_FAILED") {
          const node = data["failed_node"];
          if (typeof node === "string" && node) setFailedNode(node);
        }

        if (payload.type === "JOB_COMPLETED" || payload.type === "JOB_FAILED") {
          void fetch(`${BACKEND_URL}/api/jobs/${createdJobId}`)
            .then((res) => res.json())
            .then((result: { status?: string; failed_node?: string }) => {
              setJobFinalStatus(result.status ?? "");
              if (result.failed_node) setFailedNode(result.failed_node);
            })
            .catch(() => {
              setJobFinalStatus("");
            });
          source.close();
        }
      } catch {
        setStatusText("SSE 事件解析失败");
      }
    };

    source.onerror = () => {
      setStatusText("SSE 连接中断，请检查后端是否仍在运行");
    };

    eventSourceRef.current = source;
  };

  /* ---- Submit ---- */
  const onSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!requirement.trim()) {
      setStatusText("请先输入需求描述");
      return;
    }
    if (files.length === 0) {
      setStatusText("请至少上传一张图片");
      return;
    }

    setIsSubmitting(true);
    setLogs([]);
    setDraftTitle("");
    setDraftContent("");
    setBrowserState({ status: "idle", liveUrl: "", instructions: "" });
    setStatusText("正在创建任务...");
    setJobFinalStatus("");
    setFailedNode("");

    try {
      const form = new FormData();
      form.append("platform", platform);
      form.append("user_requirement", requirement.trim());
      form.append("max_retries", "3");
      files.forEach((file) => form.append("images", file));

      const response = await fetch(`${BACKEND_URL}/api/jobs`, {
        method: "POST",
        body: form,
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "创建任务失败");
      }
      const data = (await response.json()) as { job_id: string };
      setJobId(data.job_id);
      setStatusText(`任务已创建: ${data.job_id}`);
      connectSse(data.job_id);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : "请求失败");
    } finally {
      setIsSubmitting(false);
    }
  };

  /* ---- Resume ---- */
  const onResume = async () => {
    if (!jobId) return;
    setIsResuming(true);
    setLogs([]);
    setBrowserState({ status: "idle", liveUrl: "", instructions: "" });
    setStatusText("正在从失败节点恢复执行...");
    setJobFinalStatus("");

    try {
      const response = await fetch(`${BACKEND_URL}/api/jobs/${jobId}/resume`, {
        method: "POST",
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(text || "恢复执行失败");
      }
      const data = (await response.json()) as {
        job_id: string;
        resumed_from_node: string;
      };
      setJobId(data.job_id);
      setFailedNode("");
      setStatusText(
        `从节点 ${data.resumed_from_node} 恢复执行，新任务: ${data.job_id}`,
      );
      connectSse(data.job_id);
    } catch (error) {
      setStatusText(error instanceof Error ? error.message : "恢复执行失败");
    } finally {
      setIsResuming(false);
    }
  };

  /* ---- Status dot class ---- */
  const statusDotClass = useMemo(() => {
    if (isRunning) return "status-dot running";
    if (jobFinalStatus === "failed") return "status-dot failed";
    if (jobFinalStatus === "completed") return "status-dot completed";
    return "status-dot";
  }, [isRunning, jobFinalStatus]);

  return (
    <main className="page">
      {/* ===== Header ===== */}
      <header className="page-header">
        <h1 className="title">自媒体操盘手</h1>
        <p className="subtitle">
          上传图片，输入需求，选择平台 — 自动生成文案并打开创作中心，停在发布按钮前交由人工确认。
        </p>
      </header>

      <hr className="divider" />

      {/* ===== Form Section ===== */}
      <section className="section">
        <form onSubmit={onSubmit}>
          <div className="field">
            <label className="field-label" htmlFor="platform">
              平台
            </label>
            <select
              id="platform"
              value={platform}
              onChange={(e) => setPlatform(e.target.value as Platform)}
            >
              <option value="xhs">小红书</option>
              <option value="douyin">抖音</option>
            </select>
          </div>

          <div className="field">
            <label className="field-label" htmlFor="requirement">
              需求描述
            </label>
            <textarea
              id="requirement"
              placeholder="例如：主打真实测评风格，强调前后对比，目标女性 25-35 岁"
              value={requirement}
              onChange={(e) => setRequirement(e.target.value)}
            />
          </div>

          <div className="field">
            <label className="field-label">素材图片</label>
            <div
              className="upload-area"
              onClick={() => fileInputRef.current?.click()}
            >
              <svg
                className="upload-icon"
                viewBox="0 0 20 20"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.5"
              >
                <path d="M10 4v12M4 10h12" strokeLinecap="round" />
              </svg>
              <span className="upload-text">
                点击选择图片，支持多张
              </span>
              {files.length > 0 && (
                <span className="upload-count">
                  已选 {files.length} 张
                </span>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              />
            </div>
          </div>

          <button
            className="btn btn-primary"
            type="submit"
            disabled={isSubmitting}
          >
            {isSubmitting ? "提交中..." : "开始执行"}
          </button>
        </form>

        {/* Status bar */}
        <div className="status-bar">
          <span className={statusDotClass} />
          <span>{statusText}</span>
        </div>

        {/* Meta info */}
        {(jobId || jobFinalStatus) && (
          <div className="status-meta">
            {jobId && <span>ID: {jobId.slice(0, 12)}...</span>}
            {jobId && (
              <Link href={`/replay?job_id=${jobId}`}>查看回放</Link>
            )}
            {jobFinalStatus && <span>状态: {jobFinalStatus}</span>}
          </div>
        )}

        {/* Resume block */}
        {jobFinalStatus === "failed" && failedNode && (
          <div className="resume-block">
            <p className="resume-block-title">
              失败节点：{NODE_LABELS[failedNode] ?? failedNode}
            </p>
            <p className="resume-block-desc">
              可从失败节点恢复执行，跳过已成功的节点，节省时间和 Token。
            </p>
            <button
              className="btn btn-primary"
              onClick={onResume}
              disabled={isResuming}
            >
              {isResuming ? "恢复中..." : `从节点 ${failedNode} 重试`}
            </button>
          </div>
        )}
      </section>

      <hr className="divider" />

      {/* ===== Logs + Preview ===== */}
      <div className="content-grid">
        {/* Left: Timeline logs */}
        <section>
          <p className="section-label">实时日志</p>
          {logs.length === 0 ? (
            <p className="field-hint">任务启动后，事件流将在此显示。</p>
          ) : (
            <ul className="timeline">
              {logs.map((item, idx) => (
                <li
                  className="timeline-item"
                  key={`${item.timestamp}-${idx}`}
                >
                  <span className={getTimelineDotClass(item.type)} />
                  <div className="timeline-body">
                    <div>
                      <span className="timeline-type">{item.type}</span>
                      <span className="timeline-time">
                        {item.timestamp
                          ? new Date(item.timestamp).toLocaleTimeString()
                          : "--:--:--"}
                      </span>
                    </div>
                    <div className="timeline-msg">{item.message}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>

        {/* Right: Preview + Browser */}
        <section>
          <p className="section-label">内容预览</p>
          <div className="preview-block">
            {draftTitle ? (
              <div className="preview-title">{draftTitle}</div>
            ) : (
              <div className="preview-title preview-placeholder">
                等待生成标题...
              </div>
            )}
            {draftContent ? (
              <div className="preview-content">{draftContent}</div>
            ) : (
              <div className="preview-content preview-placeholder">
                等待生成正文...
              </div>
            )}
          </div>

          {/* Browser status */}
          <div className="browser-section">
            <p className="section-label">浏览器状态</p>
            {readyTag ? (
              <span className={readyTag.cls}>{readyTag.text}</span>
            ) : (
              <span className="field-hint">尚未到达浏览器节点</span>
            )}
            <p className="field-hint" style={{ marginTop: 8 }}>
              {browserState.instructions ||
                "Node D 完成后会在这里提示下一步。"}
            </p>
            {browserState.liveUrl ? (
              <a
                className="btn btn-ghost"
                href={browserState.liveUrl}
                target="_blank"
                rel="noreferrer"
                style={{ marginTop: 8 }}
              >
                打开 Cloud Live URL
              </a>
            ) : (
              <p className="field-hint">
                若为本地模式，请直接查看本机弹出的 Chrome 窗口。
              </p>
            )}
          </div>
        </section>
      </div>
    </main>
  );
}
