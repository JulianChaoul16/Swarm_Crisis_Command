import type { WorldEvent, WorldState } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.detail ?? `Request failed (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export const api = {
  state: () => request<WorldState>("/state"),
  events: (limit = 50) => request<WorldEvent[]>(`/events?limit=${limit}`),
  step: () => request<WorldState>("/step", { method: "POST" }),
  reset: () => request<WorldState>("/reset", { method: "POST" }),
  setRoadClosed: (roadId: string, isClosed: boolean, reason: string) =>
    request<WorldState>(`/roads/${encodeURIComponent(roadId)}`, {
      method: "POST",
      body: JSON.stringify({ is_closed: isClosed, reason }),
    }),
};
