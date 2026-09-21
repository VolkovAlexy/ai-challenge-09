// N агентов, activeAgentId, стримы, счётчики (§4).
// Единственный источник истины: стрим пишется в AgentState инкрементально,
// неактивная вкладка не рендерится, но стор обновляется.
import { computed, ref } from "vue";
import { defineStore } from "pinia";
import type { AgentDTO, LongTermDTO, MessageDTO, PatchAgentDTO, StreamEvent } from "@/api/types";
import { api } from "@/api/client";

export type AgentId = string;

export interface AgentSettingsState {
  temperature: number;
  top_p: number;
  max_tokens: number;
  stop: string[];
}

/** Клиентское состояние агента — аналог Python-класса Agent. */
export interface AgentState {
  id: AgentId;
  name: string;
  model: string;
  projectId?: string;
  settings: AgentSettingsState;
  systemPromptPath: string;
  systemPromptContent: string;
  history: MessageDTO[];
  streaming: boolean;
  compacting: boolean;
  /** локально помеченный отменённый ход: частичный ответ остаётся */
  cancelled: boolean;
  /** заметка о сжатии контекста для текущего хода */
  compactionNote: string | null;
  /** рабочая память текущей задачи (scratchpad) */
  scratchpad: string;
  /** предложение агента сохранить знание в долговременную память */
  memorySuggestion: string | null;
  /** активный профиль роли чата ("" — без него) */
  activeProfileId: string;
  /** живой стрим размышлений thinking-модели (копится до done/tool_message) */
  streamingReasoning: string;
  /** ID загруженной сессии (если агент восстановлен из сессии) */
  sessionId: string | null;
  tokensIn: number;
  tokensOut: number;
  contextUsed: number;
  contextWindow: number;
}

const ACTIVE_KEY = "my-agent.activeAgentId";
const LAST_MODEL_KEY = "my-agent.lastModel";

function stateFromDTO(dto: AgentDTO): AgentState {
  return {
    id: dto.id,
    name: dto.name,
    model: dto.model,
    projectId: dto.project_id ?? "",
    settings: { ...dto.settings, stop: [...dto.settings.stop] },
    systemPromptPath: dto.system_prompt.path,
    systemPromptContent: dto.system_prompt.content,
    history: [],
    streaming: dto.streaming,
    compacting: dto.compacting,
    cancelled: false,
    compactionNote: null,
    scratchpad: dto.scratchpad ?? "",
    memorySuggestion: dto.memory_suggestion ?? null,
    activeProfileId: dto.active_profile_id ?? "",
    streamingReasoning: "",
    sessionId: null,
    tokensIn: 0,
    tokensOut: 0,
    contextUsed: dto.context_used,
    contextWindow: dto.context_window,
  };
}

export const useAgentsStore = defineStore("agents", () => {
  const agents = ref<Record<AgentId, AgentState>>({});
  const order = ref<AgentId[]>([]);
  const activeAgentId = ref<AgentId | null>(null);
  /** Записи долговременной памяти по проектам (ключ — project_id). */
  const longtermByProject = ref<Record<string, string[]>>({});
  /** Долговременная память активного агента (проект, к которому он привязан). */
  const longtermEntries = computed(() => {
    const pid = agents.value[activeAgentId.value ?? ""]?.projectId ?? "";
    return longtermByProject.value[pid] ?? [];
  });

  function projectOf(id: AgentId): string {
    return agents.value[id]?.projectId ?? "";
  }

  // AbortController текущего стрима на агента (не реактивно, ключи динамические).
  const aborts = new Map<AgentId, AbortController>();

  const activeAgent = computed<AgentState | null>(() => {
    const id = activeAgentId.value;
    return id !== null ? (agents.value[id] ?? null) : null;
  });

  function setActive(id: AgentId): void {
    if (agents.value[id] === undefined) return;
    activeAgentId.value = id;
    localStorage.setItem(ACTIVE_KEY, id);
  }

  function restoreActive(): void {
    if (activeAgentId.value !== null && agents.value[activeAgentId.value] !== undefined) return;
    const saved = localStorage.getItem(ACTIVE_KEY);
    if (saved !== null && agents.value[saved] !== undefined) activeAgentId.value = saved;
    else activeAgentId.value = order.value[0] ?? null;
  }

  function upsert(dto: AgentDTO): AgentState {
    const existing = agents.value[dto.id];
    const state = existing ?? stateFromDTO(dto);
    state.name = dto.name;
    state.model = dto.model;
    state.projectId = dto.project_id ?? state.projectId;
    state.settings = { ...dto.settings, stop: [...dto.settings.stop] };
    state.systemPromptPath = dto.system_prompt.path;
    state.systemPromptContent = dto.system_prompt.content;
    state.streaming = dto.streaming;
    state.compacting = dto.compacting;
    state.contextUsed = dto.context_used;
    state.contextWindow = dto.context_window;
    state.scratchpad = dto.scratchpad ?? state.scratchpad;
    if (dto.memory_suggestion !== undefined) state.memorySuggestion = dto.memory_suggestion;
    if (dto.active_profile_id !== undefined) state.activeProfileId = dto.active_profile_id;
    if (existing === undefined) {
      agents.value[dto.id] = state;
      order.value.push(dto.id);
    }
    return state;
  }

  /** Восстановление после перезагрузки страницы: агенты + истории. */
  async function loadAll(): Promise<void> {
    const dtos = await api.listAgents();
    const fresh: Record<AgentId, AgentState> = {};
    const newOrder: AgentId[] = [];
    for (const dto of dtos) {
      fresh[dto.id] = stateFromDTO(dto);
      newOrder.push(dto.id);
    }
    agents.value = fresh;
    order.value = newOrder;
    restoreActive();
    await Promise.all(dtos.map((d) => loadHistory(d.id)));
  }

  async function loadHistory(id: AgentId): Promise<void> {
    const state = agents.value[id];
    if (state === undefined) return;
    state.history = await api.getMessages(id);
  }

  async function createAgent(name?: string, projectId?: string): Promise<AgentState> {
    const dto = await api.createAgent(name, projectId);
    const state = upsert(dto);
    setActive(dto.id);
    // новый чат стартует с последней выбранной модели (если она ещё доступна)
    const lastModel = localStorage.getItem(LAST_MODEL_KEY);
    if (lastModel !== null && lastModel !== dto.model) {
      try {
        await patchAgent(dto.id, { model: lastModel });
      } catch {
        /* модель пропала из конфига — оставляем дефолтную */
      }
    }
    return state;
  }

  async function closeAgent(id: AgentId): Promise<void> {
    aborts.get(id)?.abort();
    aborts.delete(id);
    await api.closeAgent(id);
    delete agents.value[id];
    const idx = order.value.indexOf(id);
    if (idx !== -1) order.value.splice(idx, 1);
    if (activeAgentId.value === id) {
      activeAgentId.value = order.value[0] ?? null;
      if (activeAgentId.value !== null) localStorage.setItem(ACTIVE_KEY, activeAgentId.value);
      else localStorage.removeItem(ACTIVE_KEY);
    }
  }

  /** Настройки применяются к агенту: PATCH на бэкенде, стор — из ответа. */
  async function patchAgent(id: AgentId, patch: PatchAgentDTO): Promise<void> {
    const dto = await api.patchAgent(id, patch);
    upsert(dto);
    // запоминаем последнюю выбранную модель — новый чат стартует с неё
    if (patch.model !== undefined) localStorage.setItem(LAST_MODEL_KEY, patch.model);
  }

  async function clearHistory(id: AgentId): Promise<void> {
    await api.clearMessages(id);
    const state = agents.value[id];
    if (state !== undefined) {
      state.history = [];
      state.tokensIn = 0;
      state.tokensOut = 0;
      state.contextUsed = 0;
    }
  }

  async function loadSession(id: AgentId, sessionId: string): Promise<void> {
    upsert(await api.loadSession(id, sessionId));
    const state = agents.value[id];
    if (state !== undefined) state.sessionId = sessionId;
    await loadHistory(id);
  }

  /** Ветка от сохранённой сессии: новый независимый агент, становится активным. */
  async function branchFromSession(sessionId: string): Promise<AgentState> {
    const dto = await api.branchSession(sessionId);
    const state = upsert(dto);
    setActive(dto.id);
    await loadHistory(dto.id);
    return state;
  }

  // --- стрим ---
  /** Запускает ход: POST messages + обработка SSE-событий в стор (§5.2). */
  async function runStream(id: AgentId, content: string, onEvent?: (ev: StreamEvent) => void): Promise<void> {
    const state = agents.value[id];
    if (state === undefined || state.streaming) return;
    state.streaming = true;
    state.cancelled = false;
    state.compactionNote = null;
    state.streamingReasoning = "";
    aborts.set(id, new AbortController());
    let streamText = "";
    try {
      for await (const ev of api.sendMessage(id, content)) {
        if (ev.event === "delta") {
          streamText += ev.content;
          applyDelta(state, streamText);
        } else {
          applyStreamEvent(state, ev);
          if (ev.event === "done" || ev.event === "tool_message") {
            streamText = ""; // финал/артефакт уже в истории; новый раунд — с нуля
            state.streamingReasoning = "";
          }
        }
        onEvent?.(ev);
      }
      if (streamText !== "") {
        // поток закрылся без done/cancelled/error — оставляем накопленное как assistant
        state.history.push({ id: `local-${Date.now()}`, role: "assistant", content: streamText });
      }
    } catch (e) {
      const status = (e as ApiErrorLike).status;
      pushError(state, status === undefined ? "network" : "http", e instanceof Error ? e.message : String(e));
    } finally {
      state.streaming = false;
      state.compacting = false;
      state.streamingReasoning = "";
      aborts.delete(id);
    }
  }

  /** Кнопка «Стоп» / Esc: локальная пометка + запрос отмены на бэкенде. */
  async function cancelStream(id: AgentId): Promise<void> {
    const state = agents.value[id];
    if (state === undefined || !state.streaming) return;
    state.cancelled = true;
    aborts.get(id)?.abort();
    try {
      await api.cancel(id);
    } catch {
      /* бэкенд сам завершит ход; пометка уже стоит */
    }
  }

  // --- память ---

  // --- долговременная память (своя у каждого проекта) ---

  function resolveProject(projectId?: string): string {
    if (projectId !== undefined) return projectId;
    const id = activeAgentId.value;
    return id !== null ? projectOf(id) : "";
  }

  function applyLongterm(dto: LongTermDTO): void {
    const pid = dto.project_id ?? resolveProject();
    longtermByProject.value[pid] = dto.entries;
  }

  /** Обновить записи долговременной памяти проекта из бэкенда. */
  async function refreshLongterm(projectId?: string): Promise<void> {
    applyLongterm(await api.getLongterm(projectId));
  }

  /** Добавить знание в долговременную память проекта. */
  async function addLongterm(content: string, projectId?: string): Promise<void> {
    applyLongterm(await api.remember(content, projectId));
  }

  /** Заменить запись долговременной памяти проекта по индексу. */
  async function updateLongterm(index: number, content: string, projectId?: string): Promise<void> {
    applyLongterm(await api.updateLongterm(index, content, projectId));
  }

  /** Удалить запись долговременной памяти проекта по индексу. */
  async function removeLongterm(index: number, projectId?: string): Promise<void> {
    applyLongterm(await api.forget(index, projectId));
  }

  /** Принять предложение памяти: знание уходит в долгосрочную память проекта агента. */
  async function acceptSuggestion(id: AgentId): Promise<void> {
    const state = agents.value[id];
    if (state === undefined || state.memorySuggestion === null) return;
    const dto = await api.acceptSuggestion(id);
    applyLongterm(dto);
    state.memorySuggestion = null;
  }

  /** Отклонить предложение памяти. */
  async function dismissSuggestion(id: AgentId): Promise<void> {
    const state = agents.value[id];
    if (state === undefined) return;
    state.memorySuggestion = null;
    try {
      await api.dismissSuggestion(id);
    } catch {
      /* предложение уже снято локально */
    }
  }

  /** Запомнить сообщение: текст — в долговременную память (LONGTERM_MEMORY.md). */
  async function rememberMessage(content: string): Promise<void> {
    await addLongterm(content);
  }

  /** Ветка от сообщения: история до index включительно; агент переключается на неё. */
  async function forkAt(id: AgentId, messageIndex: number): Promise<void> {
    const response = await api.forkAt(id, messageIndex);
    upsert(response.agent);
    const state = agents.value[id];
    if (state !== undefined) state.history = response.messages;
  }

  /** Заменить содержимое рабочей памяти (панель scratchpad). */
  async function setScratchpad(id: AgentId, content: string): Promise<void> {
    const state = agents.value[id];
    if (state === undefined) return;
    state.scratchpad = content;
    try {
      await api.putScratchpad(id, content);
    } catch {
      /* локальное состояние уже обновлено; автосохранение подхватит */
    }
  }

  return {
    agents,
    order,
    activeAgentId,
    activeAgent,
    loadAll,
    loadHistory,
    createAgent,
    closeAgent,
    patchAgent,
    clearHistory,
    loadSession,
    branchFromSession,
    runStream,
    cancelStream,
    longtermEntries,
    refreshLongterm,
    addLongterm,
    updateLongterm,
    removeLongterm,
    acceptSuggestion,
    dismissSuggestion,
    rememberMessage,
    forkAt,
    setScratchpad,
    setActive,
  };
});

interface ApiErrorLike {
  status?: number;
}

/**
 * Не-delta события стрима в состояние агента.
 * Экспортируется: используется и стором (runStream), и useChatStream,
 * чтобы оба пути обрабатывали события одинаково.
 */
export function applyStreamEvent(state: AgentState, ev: StreamEvent): void {
  switch (ev.event) {
    case "user_message":
      state.history.push(ev.message);
      break;
    case "compaction_started":
      state.compacting = true;
      break;
    case "compaction_done":
      state.compacting = false;
      state.compactionNote =
        `⇄ Контекст сжат: -${ev.removed} удалено, +${ev.summary_tokens} саммари ` +
        `(${Math.round(ev.pct_before * 100)}% → ${Math.round(ev.pct_after * 100)}%)`;
      break;
    case "reasoning_delta":
      // размышления thinking-модели: копим и складываем в стрим-сообщение
      state.streamingReasoning += ev.content;
      reasoningToStream(state, state.streamingReasoning);
      break;
    case "tool_message":
      // артефакт tool-раунда (assistant с tool_calls или результат инструмента):
      // replace стрим-заглушки либо push — порядок истории совпадает с бэкендом
      upsertDone(state, ev.message);
      break;
    case "scratchpad":
      state.scratchpad = ev.content;
      break;
    case "done":
      upsertDone(state, ev.message);
      break;
    case "cancelled":
      state.cancelled = true;
      break;
    case "memory_suggestion":
      state.memorySuggestion = ev.content;
      break;
    case "error":
      pushError(state, ev.kind, ev.detail);
      break;
    default:
      break;
  }
}

function applyDelta(state: AgentState, fullText: string): void {
  const last = state.history[state.history.length - 1];
  if (last === undefined || last.role !== "assistant") {
    state.history.push({ id: `stream-${state.id}`, role: "assistant", content: fullText });
  } else {
    last.content = fullText;
  }
}

/** Размышления пишутся в существующее стрим-сообщение или открывают новое (до контента). */
function reasoningToStream(state: AgentState, reasoning: string): void {
  const last = state.history[state.history.length - 1];
  if (last !== undefined && last.role === "assistant") {
    last.reasoning = reasoning;
  } else {
    state.history.push({ id: `stream-${state.id}`, role: "assistant", content: "", reasoning });
  }
}

function upsertDone(state: AgentState, message: MessageDTO): void {
  // точное совпадение по id (m{idx}) — артефакты tool-раундов приходят подряд
  const idx = state.history.findIndex((m) => m.id === message.id);
  if (idx !== -1) {
    state.history[idx] = message;
  } else {
    const last = state.history[state.history.length - 1];
    if (last !== undefined && last.id.startsWith("stream-")) {
      state.history[state.history.length - 1] = message; // заменяем стрим-заглушку финальным
    } else {
      state.history.push(message);
    }
  }
  const u = message.usage;
  if (u != null) { // null у tool_message (usage нет) — не должен ронять ход
    state.tokensIn += u.prompt_tokens ?? 0;
    state.tokensOut += u.completion_tokens ?? 0;
  }
  if (u?.prompt_tokens !== undefined) state.contextUsed = u.prompt_tokens;
  state.compacting = false;
}

function pushError(state: AgentState, kind: string, detail: string): void {
  state.history.push({
    id: `err-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role: "system",
    content: detail,
    error: { kind, detail },
  });
}
