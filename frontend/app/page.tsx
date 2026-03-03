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
  const [browserState, setBrowserState] = useState<BrowserReadyState>({
    status: "idle",
    liveUrl: "",
    instructions: "",
  });

  const eventSourceRef = useRef<EventSource | null>(null);

  const readyTag = useMemo(() => {
    if (browserState.status === "ready") return { cls: "tag tag-ok", text: "浏览器已就绪" };
    if (browserState.status === "need_login") return { cls: "tag tag-warn", text: "需要先登录" };
    if (browserState.status === "failed") return { cls: "tag tag-warn", text: "浏览器失败" };
    return null;
  }, [browserState.status]);

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
      }
    };
  }, []);

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

        if (payload.type === "JOB_COMPLETED" || payload.type === "JOB_FAILED") {
          void fetch(`${BACKEND_URL}/api/jobs/${createdJobId}`)
            .then((res) => res.json())
            .then((result: { status?: string }) => {
              setJobFinalStatus(result.status ?? "");
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

  return (
    <main className="page">
      <h1 className="title">Autonomous Content Agent / 自媒体操盘手</h1>
      <p className="subtitle">
        上传图片 + 输入需求 + 选择平台，自动生成文案并打开创作中心，停在发布按钮前交由人工确认。
      </p>

      <div className="grid">
        <section className="card">
          <form onSubmit={onSubmit}>
            <div className="field">
              <label htmlFor="platform">平台</label>
              <select id="platform" value={platform} onChange={(e) => setPlatform(e.target.value as Platform)}>
                <option value="xhs">小红书 (xhs)</option>
                <option value="douyin">抖音 (douyin)</option>
              </select>
            </div>

            <div className="field">
              <label htmlFor="requirement">需求描述</label>
              <textarea
                id="requirement"
                placeholder="例如：主打真实测评风格，强调前后对比，目标女性 25-35 岁"
                value={requirement}
                onChange={(e) => setRequirement(e.target.value)}
              />
            </div>

            <div className="field">
              <label htmlFor="images">上传图片（可多张）</label>
              <input
                id="images"
                type="file"
                accept="image/*"
                multiple
                onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
              />
              <span className="hint">当前已选 {files.length} 张图片</span>
            </div>

            <button className="btn btn-primary" type="submit" disabled={isSubmitting}>
              {isSubmitting ? "提交中..." : "开始执行"}
            </button>
          </form>
          <p className="hint" style={{ marginTop: 12 }}>
          当前状态：{statusText}
        </p>
          {jobId ? <p className="hint">Job ID: {jobId}</p> : null}
          {jobId ? (
            <p className="hint">
              <Link href={`/replay?job_id=${jobId}`}>Open Run Replay</Link>
            </p>
          ) : null}
          {jobFinalStatus ? <p className="hint">最终状态：{jobFinalStatus}</p> : null}
        </section>

        <section className="card">
          <h3 style={{ marginTop: 0 }}>实时日志（SSE）</h3>
          <ul className="log-list">
            {logs.map((item, idx) => (
              <li className="log-item" key={`${item.timestamp}-${idx}`}>
                <div>
                  <strong>{item.type}</strong> ·{" "}
                  <span>
                    {item.timestamp ? new Date(item.timestamp).toLocaleTimeString() : "--:--:--"}
                  </span>
                </div>
                <div>{item.message}</div>
              </li>
            ))}
          </ul>
        </section>
      </div>

      <div className="grid">
        <section className="card">
          <h3 style={{ marginTop: 0 }}>标题正文预览</h3>
          <div className="preview-title">{draftTitle || "（等待生成标题）"}</div>
          <div className="preview-content">{draftContent || "（等待生成正文）"}</div>
        </section>

        <section className="card">
          <h3 style={{ marginTop: 0 }}>浏览器就绪状态</h3>
          {readyTag ? <span className={readyTag.cls}>{readyTag.text}</span> : <span className="hint">尚未到达浏览器节点</span>}
          <p className="hint" style={{ marginTop: 10 }}>
            {browserState.instructions || "Node D 完成后会在这里提示下一步。"}
          </p>
          {browserState.liveUrl ? (
            <a className="btn btn-ghost" href={browserState.liveUrl} target="_blank" rel="noreferrer">
              打开 Cloud Live URL
            </a>
          ) : (
            <p className="hint">
              若为本地 Real Browser 模式，请直接查看本机弹出的 Chrome 窗口。
            </p>
          )}
        </section>
      </div>
    </main>
  );
}
