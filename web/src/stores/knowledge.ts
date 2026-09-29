// RAG-знания: статус индекса + эфемерные настройки (on/off, нарезка чанков).
// Настройки живут только в оперативке бэкенда — при рестарте сбрасываются.
import { defineStore } from 'pinia';
import { ref } from 'vue';
import { api } from '@/api/client';
import type { KnowledgeDTO, KnowledgePatchRequest } from '@/api/types';

export const useKnowledgeStore = defineStore('knowledge', () => {
  const knowledge = ref<KnowledgeDTO | null>(null);
  const loaded = ref(false);
  const loadError = ref<string | null>(null);

  async function load(): Promise<void> {
    try {
      knowledge.value = await api.getKnowledge();
      loadError.value = null;
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    } finally {
      loaded.value = true;
    }
  }

  async function update(body: KnowledgePatchRequest): Promise<void> {
    try {
      knowledge.value = await api.patchKnowledge(body);
      loadError.value = null;
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    }
  }

  return { knowledge, loaded, loadError, load, update };
});
