"use client";

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init: RequestInit = {}, token?: string | null): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`/api/backend${path}`, { ...init, headers, cache: "no-store" });
  const contentType = response.headers.get("content-type") ?? "";
  const payload = contentType.includes("application/json") ? await response.json() : await response.text();
  if (!response.ok) {
    const detail = typeof payload === "object" && payload ? String(payload.detail ?? "Request failed") : String(payload);
    throw new ApiError(response.status, detail);
  }
  return payload as T;
}

export function json(body: unknown): RequestInit {
  return { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

export type SseEvent = { event: string; data: unknown };

/** Stream a POST response encoded as Server-Sent Events.
 *
 * EventSource cannot attach the workspace's bearer token or send the chat
 * request body, so Evidence Chat uses fetch + ReadableStream instead.
 */
export async function streamSse(
  path: string,
  init: RequestInit,
  token: string | null | undefined,
  onEvent: (event: SseEvent) => void,
): Promise<void> {
  const headers = new Headers(init.headers);
  headers.set("Accept", "text/event-stream");
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch(`/api/backend${path}`, { ...init, headers, cache: "no-store" });
  if (!response.ok || !response.body) {
    const payload = await response.text();
    throw new ApiError(response.status, payload || "The stream could not be opened.");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  const dispatch = (frame: string) => {
    const lines = frame.split(/\r?\n/);
    const event = lines.find((line) => line.startsWith("event:"))?.slice(6).trim() || "message";
    const rawData = lines.filter((line) => line.startsWith("data:")).map((line) => line.slice(5).trimStart()).join("\n");
    if (!rawData) return;
    try { onEvent({ event, data: JSON.parse(rawData) }); }
    catch { onEvent({ event, data: rawData }); }
  };

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    frames.forEach(dispatch);
    if (done) break;
  }
  if (buffer.trim()) dispatch(buffer);
}
