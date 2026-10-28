import type { ReportFields, WorldEvent, WorldState } from "./types";

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly replacementPlanId: string | null = null,
  ) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const detail = typeof body?.detail === "string" ? body.detail : `Request failed (${response.status})`;
    throw new ApiError(detail, response.status, body?.replacement_plan_id ?? null);
  }
  return response.json() as Promise<T>;
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  state: () => request<WorldState>("/state"),
  events: (limit = 200) => request<WorldEvent[]>(`/events?limit=${limit}`),
  reset: () => post<WorldState>("/reset"),
  advance: () => post<WorldState>("/clock/advance"),
  submitReport: (content: string, source: string, fields?: Partial<ReportFields>) =>
    post<WorldState>("/reports", { content, source, fields }),
  clarifyReport: (id: string, fields: Partial<ReportFields>) =>
    post<WorldState>(`/reports/${id}/clarify`, { fields }),
  confirmReport: (id: string) => post<WorldState>(`/reports/${id}/confirm`),
  rejectReport: (id: string, reason: string) => post<WorldState>(`/reports/${id}/reject`, { reason }),
  injectDisruption: (key: string) => post<WorldState>(`/scenario/disruptions/${key}`),
  propose: () => post<WorldState>("/plans/propose"),
  approve: (planId: string, worldVersion: number) =>
    post<WorldState>(`/plans/${planId}/approve`, { world_version: worldVersion }),
  rejectPlan: (planId: string, reason: string) => post<WorldState>(`/plans/${planId}/reject`, { reason }),
};
