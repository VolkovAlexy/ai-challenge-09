import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "@/api/client";
import type { SessionInfoDTO } from "@/api/types";

const POLL_MS = 15000;

export const useSessionsStore = defineStore("sessions", () => {
  const sessions = ref<SessionInfoDTO[]>([]);
  const loaded = ref(false);
  const loadError = ref<string | null>(null);
  const hasMore = ref(false);
  let pollTimer: ReturnType<typeof setInterval> | null = null;

  async function load(limit?: number, offset?: number): Promise<void> {
    try {
      const result = await api.listSessions({ limit, offset });
      sessions.value = result;
      hasMore.value = limit !== undefined && result.length >= limit;
      loadError.value = null;
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    } finally {
      loaded.value = true;
    }
  }

  async function reload(): Promise<void> {
    await load(20, 0);
  }

  /** Загрузить все сессии (без пагинации) — для группировки по проектам. */
  async function loadAll(): Promise<void> {
    await load(undefined, 0);
  }

  /** Фоновое обновление флагов планировщика (иконки/бейджи) у сессий. */
  function startPolling(): void {
    if (pollTimer !== null) return;
    pollTimer = setInterval(() => {
      void loadAll();
    }, POLL_MS);
  }

  function stopPolling(): void {
    if (pollTimer !== null) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  async function remove(sessionId: string): Promise<void> {
    await api.deleteSession(sessionId);
    await reload();
  }

  async function rename(sessionId: string, title: string): Promise<void> {
    await api.renameSession(sessionId, title);
    await reload();
  }

  return {
    sessions,
    loaded,
    loadError,
    hasMore,
    load,
    reload,
    loadAll,
    startPolling,
    stopPolling,
    remove,
    rename,
  };
});