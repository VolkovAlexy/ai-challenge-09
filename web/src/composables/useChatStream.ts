// Запуск/отмена стрима одного агента с батчингом дельт (§7.2).
// Дельты копятся в буфер; стор обновляется раз в ~100 мс (таймер),
// не на каждый токен. После done — финальный flush. Отмена — cancelStream.
import { onUnmounted } from "vue";
import { api } from "@/api/client";
import type { StreamEvent } from "@/api/types";
import { useAgentsStore, applyStreamEvent, type AgentState } from "@/stores/agents";

const FLUSH_MS = 100;

export function useChatStream(agentId: () => string | null, onEvent?: (ev: StreamEvent) => void) {
  const store = useAgentsStore();

  let buffer = "";
  let timer: ReturnType<typeof setInterval> | null = null;
  let target: AgentState | null = null;
  let flushCount = 0; // наблюдаемо в тестах: N дельт -> 1 обновление

  function flush(): void {
    if (timer !== null) {
      clearInterval(timer);
      timer = null;
    }
    if (target === null || buffer === "") return;
    const last = target.history[target.history.length - 1];
    if (last !== undefined && last.role === "assistant") last.content = buffer;
    else target.history.push({ id: `stream-${target.id}`, role: "assistant", content: buffer });
    flushCount += 1;
  }

  /** Отправка сообщения активному агенту с батчингом дельт. */
  async function send(text: string): Promise<void> {
    const id = agentId();
    const state = id !== null ? store.agents[id] : undefined;
    if (id === null || state === undefined || state.streaming) return;
    buffer = "";
    target = state;
    state.streaming = true;
    state.cancelled = false;
    state.compactionNote = null;
    state.streamingReasoning = "";
    state.subagents = [];
    try {
      for await (const ev of api.sendMessage(id, text)) {
        if (ev.event === "delta") {
          buffer += ev.content;
          if (timer === null) timer = setInterval(flush, FLUSH_MS);
          continue; // дельты попадают в стор только через flush
        }
        applyNonDelta(state, ev);
        if (ev.event === "done" || ev.event === "cancelled") {
          buffer = ""; // финал уже в истории
          state.streamingReasoning = "";
        } else if (ev.event === "tool_message") {
          flush(); // текст прошлого раунда уже заменён авторитетным сообщением
          buffer = ""; // дельты нового раунда начинаются с нуля
          state.streamingReasoning = "";
        } else if (ev.event === "error") flush();
        onEvent?.(ev);
      }
      flush(); // финальный flush: буфер без терминала не теряется
    } catch (e) {
      flush();
      const status = (e as { status?: number }).status;
      const kind = status === 0 ? "network" : status === undefined ? "network" : "http";
      const detail = e instanceof Error ? e.message : String(e);
      pushError(state, kind, detail);
    } finally {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
      state.streaming = false;
      state.compacting = false;
      state.streamingReasoning = "";
      buffer = "";
      target = null;
    }
  }

  /** Кнопка «Стоп» / Esc при стриме. */
  async function cancel(): Promise<void> {
    const id = agentId();
    if (id !== null) await store.cancelStream(id);
  }

  onUnmounted(() => {
    if (timer !== null) clearInterval(timer);
    timer = null;
  });

  return { send, cancel, flushCount: () => flushCount };
}

function applyNonDelta(state: AgentState, ev: StreamEvent): void {
  // единая обработка со стором (runStream) — см. applyStreamEvent в stores/agents.ts
  applyStreamEvent(state, ev);
}

function pushError(state: AgentState, kind: string, detail: string): void {
  state.history.push({
    id: `err-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role: "system",
    content: detail,
    error: { kind, detail },
  });
}
