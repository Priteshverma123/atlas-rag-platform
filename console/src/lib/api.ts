// Thin typed client for the Atlas API (/api → atlas-api) and Pulse (/pulse → pulse server).
// Keys live in localStorage for this local console; production would sit behind SSO.

export type Session = { apiKey: string; pulseKey: string; pulseUrl: string; tenantId?: string; collectionId?: string };

const KEY = "atlas.session";

export function getSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Session) : null;
  } catch {
    return null;
  }
}

export function setSession(s: Session | null) {
  try {
    if (s) localStorage.setItem(KEY, JSON.stringify(s));
    else localStorage.removeItem(KEY);
  } catch {
    /* private mode: session lasts for the tab only */
  }
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public body?: unknown) {
    super(message);
  }
}

async function request<T>(base: string, key: string | undefined, path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (key) headers.set("Authorization", `Bearer ${key}`);
  if (init.body && !(init.body instanceof FormData) && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetch(`${base}${path}`, { ...init, headers });
  if (!res.ok) {
    let body: any = undefined;
    try { body = await res.json(); } catch { /* not json */ }
    const msg = body?.detail ?? body?.title ?? body?.error?.message ?? res.statusText;
    if (res.status === 401) window.dispatchEvent(new Event("atlas:unauthorized"));
    throw new ApiError(res.status, typeof msg === "string" ? msg : JSON.stringify(msg), body);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("/api", getSession()?.apiKey, path),
  post: <T,>(path: string, body?: unknown) =>
    request<T>("/api", getSession()?.apiKey, path, { method: "POST", body: body instanceof FormData ? body : JSON.stringify(body ?? {}) }),
  patch: <T,>(path: string, body: unknown) => request<T>("/api", getSession()?.apiKey, path, { method: "PATCH", body: JSON.stringify(body) }),
  del: <T,>(path: string) => request<T>("/api", getSession()?.apiKey, path, { method: "DELETE" }),
  raw: request,
};

export const pulse = {
  get: <T,>(path: string) => {
    const s = getSession();
    return request<T>(s?.pulseUrl ?? "/pulse", s?.pulseKey, path);
  },
  post: <T,>(path: string, body: unknown) => {
    const s = getSession();
    return request<T>(s?.pulseUrl ?? "/pulse", s?.pulseKey, path, { method: "POST", body: JSON.stringify(body) });
  },
};

/** POST + Server-Sent Events over fetch (EventSource can't POST or send auth headers). */
export async function streamSSE(path: string, body: unknown, onEvent: (event: string, data: any) => void,
                                signal?: AbortSignal, method = "POST"): Promise<void> {
  const res = await fetch(`/api${path}`, {
    method, signal,
    headers: { Authorization: `Bearer ${getSession()?.apiKey ?? ""}`, "Content-Type": "application/json", Accept: "text/event-stream" },
    body: method === "POST" ? JSON.stringify(body) : undefined,
  });
  if (!res.ok || !res.body) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail ?? msg; } catch { /* ignore */ }
    throw new ApiError(res.status, msg);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx: number;
    while ((idx = buf.search(/\r?\n\r?\n/)) >= 0) {
      const block = buf.slice(0, idx);
      buf = buf.slice(idx).replace(/^\r?\n\r?\n/, "");
      let event = "message";
      const data: string[] = [];
      for (const line of block.split(/\r?\n/)) {
        if (line.startsWith("event:")) event = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).trimStart());
      }
      if (!data.length) continue;
      const joined = data.join("\n");
      try { onEvent(event, JSON.parse(joined)); } catch { onEvent(event, joined); }
    }
  }
}
