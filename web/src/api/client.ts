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
  ProfileDTO,
  ProjectDTO,
  SessionInfoDTO,
  StreamEvent,
  TaskCommandRequest,
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

/** Строит query-строку `?project_id=…` или пустую строку. */
function query(projectId?: string): string {
  if (!projectId) return "";
  return "?project_id=" + encodeURIComponent(projectId);
}

export const api = {
  getConfig: () => request<ConfigDTO>("GET", "/config"),
  getCommands: () => request<CommandDTO[]>("GET", "/commands"),
  getSystemPrompt: () => request<{ path: string; content: string }>("GET", "/system-prompt"),
  putSystemPrompt: (path: string) =>
    request<{ path: string; content: string }>("PUT", "/system-prompt", { path }),

  listAgents: () => request<AgentDTO[]>("GET", "/agents"),
  createAgent: (name?: string, projectId?: string) =>
    request<AgentDTO>("POST", "/agents", { name, project_id: projectId }),
  closeAgent: (id: string) => request<unknown>("DELETE", `/agents/${id}`),
  patchAgent: (id: string, patch: PatchAgentDTO) =>
    request<AgentDTO>("PATCH", `/agents/${id}`, patch),
  getMessages: (id: string) => request<MessageDTO[]>(`GET`, `/agents/${id}/messages`),
  clearMessages: (id: string) => request<unknown>("DELETE", `/agents/${id}/messages`),

  listSessions: (params?: { limit?: number; offset?: number; project_id?: string }) => {
    const qs = new URLSearchParams();
    if (params?.limit !== undefined) qs.set("limit", String(params.limit));
    if (params?.offset !== undefined) qs.set("offset", String(params.offset));
    if (params?.project_id !== undefined) qs.set("project_id", params.project_id);
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

  // --- проекты (Слой 1) ---

  listProjects: () => request<ProjectDTO[]>("GET", "/projects"),
  createProject: (name: string) =>
    request<ProjectDTO>("POST", "/projects", { name }),
  getProject: (id: string) => request<ProjectDTO>("GET", `/projects/${id}`),
  renameProject: (id: string, name: string) =>
    request<ProjectDTO>("PATCH", `/projects/${id}`, { name }),
  deleteProject: (id: string) => request<{ ok: boolean }>("DELETE", `/projects/${id}`),

  // --- профили (глобальный пул + привязка к проекту) ---

  listProfiles: () => request<ProfileDTO[]>("GET", "/profiles"),
  createProfile: (name: string, content: string) =>
    request<ProfileDTO>("POST", "/profiles", { name, content }),
  updateProfile: (id: string, patch: { name?: string; content?: string }) =>
    request<ProfileDTO>("PATCH", `/profiles/${id}`, patch),
  deleteProfile: (id: string) => request<{ ok: boolean }>("DELETE", `/profiles/${id}`),
  getProjectProfiles: (projectId: string) =>
    request<ProfileDTO[]>("GET", `/projects/${projectId}/profiles`),
  setProjectProfiles: (projectId: string, profileIds: string[]) =>
    request<ProfileDTO[]>("PUT", `/projects/${projectId}/profiles`, { profile_ids: profileIds }),

  cancel: (id: string) => request<unknown>("POST", `/agents/${id}/cancel`),

  // --- память ---

  /** Рабочая память: заменить содержимое scratchpad. */
  putScratchpad: (id: string, content: string) =>
    request<{ content: string }>("PUT", `/agents/${id}/scratchpad`, { content }),

  /** Команда состоянию задачи (конечный автомат): POST завершает → возвращает агента. */
  taskCommand: (id: string, body: TaskCommandRequest) =>
    request<AgentDTO>("POST", `/agents/${id}/task`, body),

  /** Ветка от сообщения: копия истории до message_index включительно + переключение. */
  forkAt: (id: string, messageIndex: number) =>
    request<ForkResponseDTO>("POST", `/agents/${id}/fork`, { message_index: messageIndex }),

  /** Долговременная память проекта: содержимое + записи. */
  getLongterm: (projectId?: string) =>
    request<LongTermDTO>("GET", `/longterm${query(projectId)}`),

  /** Добавить знание в долговременную память проекта. */
  remember: (content: string, projectId?: string) =>
    request<LongTermDTO>("POST", `/longterm${query(projectId)}`, { content }),

  /** Удалить запись долговременной памяти проекта по индексу. */
  forget: (index: number, projectId?: string) =>
    request<LongTermDTO>("DELETE", `/longterm/${index}${query(projectId)}`),

  /** Заменить запись долговременной памяти проекта по индексу. */
  updateLongterm: (index: number, content: string, projectId?: string) =>
    request<LongTermDTO>("PUT", `/longterm/${index}${query(projectId)}`, { content }),

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
