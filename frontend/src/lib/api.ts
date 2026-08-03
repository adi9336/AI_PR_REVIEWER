// Server-side API client (M15 dashboard). Never imported by client
// components — the GOVERNANCE_API_KEY must not reach the browser.

export interface ReviewSummary {
  id: string;
  repo: string;
  pr_number: number | null;
  status: string;
  overall_confidence: number | null;
  created_at: string | null;
}

export interface Finding {
  id: string;
  agent_type: string;
  severity: string;
  category: string;
  summary: string;
  file_path: string;
  line_start: number | null;
  line_end: number | null;
  suggestion: string;
  confidence: number | null;
  rationale: string;
}

export interface ReviewDetail {
  review: {
    id: string;
    repo: string;
    pr_number: number | null;
    delivery_uuid: string | null;
    status: string;
    overall_confidence: number | null;
    github_review_id: number | null;
    created_at: string | null;
    posted_at: string | null;
  };
  findings: Finding[];
  events_count: number;
}

export interface TraceEvent {
  ts: string;
  review_id: string;
  agent: string;
  event_type: string;
  model: string | null;
  tokens_in: number | null;
  tokens_out: number | null;
  cost_usd: number | null;
  latency_ms: number | null;
  outcome: string | null;
  confidence: number | null;
  payload: Record<string, unknown> | null;
}

export interface HitlItem {
  id: string;
  review_id: string;
  reason: string;
  state: string;
  created_at: string;
}

const API_BASE = process.env.API_BASE_URL ?? "http://localhost:8000";
const GOV_KEY = process.env.GOVERNANCE_API_KEY ?? "";

async function get<T>(path: string, governance = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (governance) {
    if (!GOV_KEY) {
      throw new Error(
        "GOVERNANCE_API_KEY is not set — audit/trace routes are key-protected"
      );
    }
    headers["X-API-Key"] = GOV_KEY;
  }
  const res = await fetch(`${API_BASE}${path}`, {
    headers,
    cache: "no-store",
  });
  if (!res.ok) {
    throw new Error(`GET ${path} -> ${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

export const listReviews = (): Promise<{ reviews: ReviewSummary[]; count: number }> =>
  get("/reviews");

export const getReview = (id: string): Promise<ReviewDetail> =>
  get(`/reviews/${id}`);

export const getTrace = (
  id: string
): Promise<{ review_id: string; events: TraceEvent[]; count: number }> =>
  get(`/audit/reviews/${id}/trace`, true);

export const getHitlQueue = (): Promise<HitlItem[]> => get("/hitl/queue");
