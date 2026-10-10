// Запуск/отмена стрима одного агента с батчингом дельт (§7.2).
// Дельты копятся в буфер; стор обновляется раз в ~100 мс (таймер),
// не на каждый токен. После done — финальный flush. Отмена — cancelStream.
import { api } from '@/api/client';
import type { StreamEvent } from '@/api/types';
import { type AgentState, applyStreamEvent, useAgentsStore } from '@/stores/agents';

const FLUSH_MS = 100;

export function useChatStream(agentId: () => string | null, onEvent?: (ev: StreamEvent) => void) {
  const store = useAgentsStore();

  let flushCount = 0; // наблюдаемо в тестах: N дельт -> 1 обновление

  /** Отправка сообщения активному агенту с батчингом дельт. */
  async function send(text: string): Promise<void> {
    const id = agentId();
    const state = id !== null ? store.agents[id] : undefined;
    if (id === null || state === undefined || state.streaming) return;
    let buffer = '';
    let timer: ReturnType<typeof setInterval> | null = null;
    const target = state;

    function flush(): void {
      if (timer !== null) { clearInterval(timer); timer = null; }
      if (buffer === '') return;
      const stub = `stream-${target.id}`;
      const last = target.history[target.history.length - 1];
      // дописываем только собственную заглушку; готовый ответ/артефакт не трогаем
      if (last !== undefined && last.id === stub) last.content = buffer;
      else target.history.push({ id: stub, role: 'assistant', content: buffer });
      flushCount += 1;
    }
    state.streaming = true;
    state.cancelled = false;
    state.compactionNote = null;
    state.streamingReasoning = '';
    state.subagents = [];
    try {
      for await (const ev of api.sendMessage(id, text)) {
        if (ev.event === 'delta') {
          buffer += ev.content;
          if (timer === null) timer = setInterval(flush, FLUSH_MS);
          continue; // дельты попадают в стор только через flush
        }
        applyNonDelta(state, ev);
        if (ev.event === 'done' || ev.event === 'cancelled') {
          buffer = ''; // финал уже в истории
          state.streamingReasoning = '';
        } else if (ev.event === 'tool_message') {
          buffer = ''; // текст раунда уже заменён авторитетным сообщением (upsertDone)
          state.streamingReasoning = ''; // дельты нового раунда начинаются с нуля
        } else if (ev.event === 'error') flush();
        onEvent?.(ev);
      }
      flush(); // финальный flush: буфер без терминала не теряется
    } catch (e) {
      flush();
      const status = (e as { status?: number }).status;
      const kind = status === 0 ? 'network' : status === undefined ? 'network' : 'http';
      const detail = e instanceof Error ? e.message : String(e);
      pushError(state, kind, detail);
    } finally {
      if (timer !== null) {
        clearInterval(timer);
        timer = null;
      }
      state.streaming = false;
      state.compacting = false;
      state.streamingReasoning = '';
    }
  }

  /** Кнопка «Стоп» / Esc при стриме. */
  async function cancel(): Promise<void> {
    const id = agentId();
    if (id !== null) await store.cancelStream(id);
  }

  return { send, cancel, flushCount: () => flushCount };
}

function applyNonDelta(state: AgentState, ev: StreamEvent): void {
  // единая обработка со стором (runStream) — см. applyStreamEvent в stores/agents.ts
  applyStreamEvent(state, ev);
}

function pushError(state: AgentState, kind: string, detail: string): void {
  state.history.push({
    id: `err-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`,
    role: 'system',
    content: detail,
    error: { kind, detail },
  });
}
