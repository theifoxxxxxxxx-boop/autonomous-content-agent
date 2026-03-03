export type Platform = "douyin" | "xhs";

export interface StreamEvent {
  type: string;
  job_id: string;
  message: string;
  timestamp: string;
  data: Record<string, unknown>;
}

export interface JobEventsResponse {
  job_id: string;
  status: string;
  total: number;
  events: StreamEvent[];
}
