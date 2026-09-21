// Fetch-обёртка: все REST-эндпоинты + SSE-отправка сообщения (§5.1).
// Ошибки бэкенда ({detail}) маппятся в ApiError со статусом.

import type {
  AgentDTO,
  CommandDTO,
  ConfigDTO,
  ForkResponseDTO,
  LongTermDTO,
  MessageDTO,
  PatchAgentDTO,
  SessionInfoDTO,
  StreamEvent,
} from "./types";
import { sseEvents } from "./sse";

const BASE = "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    detail: string,
  ) {
    super(detail);
    this.name = "ApiError";
  }
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(BASE + path, {
      method,
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "нет соединения с бэкендом");
  }
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const data = (await response.json()) as { detail?: string };
      if (typeof data.detail === "string") detail = data.detail;
    } catch {
      /* тело не JSON — оставляем HTTP-статус */
    }
    throw new ApiError(response.status, detail);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  getConfig: () => request<ConfigDTO>("GET", "/config"),
  getCommands: () => request<CommandDTO[]>("GET", "/commands"),
  getSystemPrompt: () => request<{ path: string; content: string }>("GET", "/system-prompt"),
  putSystemPrompt: (path: string) =>
    request<{ path: string; content: string }>("PUT", "/system-prompt", { path }),

  listAgents: () => request<AgentDTO[]>("GET", "/agents"),
  createAgent: (name?: string) => request<AgentDTO>("POST", "/agents", name ? { name } : {}),
  closeAgent: (id: string) => request<unknown>("DELETE", `/agents/${id}`),
  patchAgent: (id: string, patch: PatchAgentDTO) =>
    request<AgentDTO>("PATCH", `/agents/${id}`, patch),
  getMessages: (id: string) => request<MessageDTO[]>(`GET`, `/agents/${id}/messages`),
  clearMessages: (id: string) => request<unknown>("DELETE", `/agents/${id}/messages`),

  listSessions: (params?: { limit?: number; offset?: number }) => {
    const qs = new URLSearchParams();
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.offset !== undefined) qs.set("offset", String(params.offset));
    const query = qs.size > 0 ? "?" + qs.toString() : "";
    return request<SessionInfoDTO[]>("GET", `/sessions${query}`);
  },
  loadSession: (id: string, sessionId: string) =>
    request<AgentDTO>("POST", `/agents/${id}/load-session`, { session_id: sessionId }),
  exportSession: (id: string, path?: string) =>
    request<{ path: string }>("POST", `/agents/${id}/export`, path ? { path } : {}),
  deleteSession: (sessionId: string) =>
    request<{ ok: boolean }>("DELETE", `/sessions/${sessionId}`),
  branchSession: (sessionId: string) =>
    request<AgentDTO>("POST", `/sessions/${sessionId}/branch`),
  renameSession: (sessionId: string, title: string) =>
    request<{ ok: boolean }>("PATCH", `/sessions/${sessionId}`, { title }),

  cancel: (id: string) => request<unknown>("POST", `/agents/${id}/cancel`),

  // --- память ---

  /** Рабочая память: заменить содержимое scratchpad. */
  putScratchpad: (id: string, content: string) =>
    request<{ content: string }>("PUT", `/agents/${id}/scratchpad`, { content }),

  /** Ветка от сообщения: копия истории до message_index включительно + переключение. */
  forkAt: (id: string, messageIndex: number) =>
    request<ForkResponseDTO>("POST", `/agents/${id}/fork`, { message_index: messageIndex }),

  /** Долговременная память: содержимое + записи. */
  getLongterm: () => request<LongTermDTO>("GET", "/longterm"),

  /** Добавить знание в долговременную память. */
  remember: (content: string) =>
    request<LongTermDTO>("POST", "/longterm", { content }),

  /** Удалить запись долговременной памяти по индексу. */
  forget: (index: number) => request<LongTermDTO>("DELETE", `/longterm/${index}`),

  /** Заменить запись долговременной памяти по индексу. */
  updateLongterm: (index: number, content: string) =>
    request<LongTermDTO>("PUT", `/longterm/${index}`, { content }),

  /** Принять предложение агента (memory_suggestion) — знание уходит в долгосрочную память. */
  acceptSuggestion: (id: string) => request<LongTermDTO>("POST", `/agents/${id}/memory-suggestion/accept`),

  /** Отклонить предложение агента. */
  dismissSuggestion: (id: string) =>
    request<unknown>("POST", `/agents/${id}/memory-suggestion/dismiss`),

  /** Отправка сообщения; ответ — SSE-поток событий §5.2. */
  async *sendMessage(id: string, content: string): AsyncGenerator<StreamEvent> {
    let response: Response;
    try {
      response = await fetch(`${BASE}/agents/${id}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content }),
      });
    } catch {
      throw new ApiError(0, "нет соединения с бэкендом");
    }
    if (!response.ok) {
      let detail = `HTTP ${response.status}`;
      try {
        const data = (await response.json()) as { detail?: string };
        if (typeof data.detail === "string") detail = data.detail;
      } catch {
        /* тело не JSON */
      }
      throw new ApiError(response.status, detail);
    }
    for await (const raw of sseEvents(response)) {
      let parsed: StreamEvent;
      try {
        const obj = JSON.parse(raw) as Record<string, unknown> & { event?: string };
        const { event, ...fields } = obj;
        parsed = { event: event ?? "error", ...fields } as unknown as StreamEvent;
      } catch {
        parsed = { event: "error", kind: "network", detail: `некорректное SSE-событие: ${raw.slice(0, 100)}` };
      }
      yield parsed;
    }
  },
};
