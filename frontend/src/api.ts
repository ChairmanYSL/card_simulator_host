import type {
  CaseDetail,
  HistorySummary,
  LibraryIssue,
  RunDetail,
  SchemeNode,
  SchemeTemplate,
  Snapshot,
} from "./types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail))
        detail = body.detail.map((d: unknown) =>
          typeof d === "object" && d !== null && "msg" in (d as Record<string, unknown>)
            ? String((d as Record<string, unknown>).msg)
            : JSON.stringify(d)).join("; ");
    } catch {
      /* keep default detail */
    }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

function post<T>(path: string, body?: unknown): Promise<T> {
  return request<T>(path, {
    method: "POST",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export const api = {
  health: () => request<{ status: string; busy: boolean; devices: unknown; session: unknown }>("/api/health"),
  tree: () => request<{ schemes: SchemeNode[]; issues: LibraryIssue[] }>("/api/tree"),
  listCases: (scheme: string, mode: string) =>
    request<{ cases: { case_id: string; title: string; summary: string; entry_mode: string }[] }>(
      `/api/cases/${scheme}/${mode}`),
  caseDetail: (scheme: string, mode: string, caseId: string) =>
    request<CaseDetail>(`/api/cases/${scheme}/${mode}/${caseId}`),
  schemeTemplate: (scheme: string) =>
    request<SchemeTemplate>(`/api/scheme/${scheme}/template`),
  validateCase: (payload: unknown) =>
    post<{ ok: boolean; errors: string[] }>("/api/cases/validate", payload),
  addCase: (payload: unknown) =>
    post<{ ok: boolean; case_id: string; group_id: string | null; path: string }>("/api/cases", payload),
  validateGroup: (payload: unknown) =>
    post<{ ok: boolean; errors: string[] }>("/api/groups/validate", payload),
  addGroup: (payload: unknown) =>
    post<{ ok: boolean; group_id: string; path: string }>("/api/groups", payload),
  snapshot: () => request<Snapshot>("/api/run"),
  startRun: (payload: {
    scheme: string; mode: string; kind: string; case_ids?: string[];
    group_id?: string | null;
    resume_from_run?: string | null; on_failure?: string | null; retry_count?: number | null;
  }) => post<{ ok: boolean; run_id: string }>("/api/run", payload),
  stop: () => post<{ ok: boolean; message: string }>("/api/run/stop"),
  abort: () => post<{ ok: boolean; message: string }>("/api/run/abort"),
  skip: () => post<{ ok: boolean; message: string }>("/api/run/skip"),
  history: () => request<{ runs: HistorySummary[] }>("/api/history"),
  historyDetail: (runId: string) => request<RunDetail>(`/api/history/${runId}`),
};
