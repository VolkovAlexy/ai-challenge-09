// Конфиг с бэкенда: провайдеры, модели, дефолты. Только чтение.
import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "@/api/client";
import type { ConfigDTO } from "@/api/types";

export const useConfigStore = defineStore("config", () => {
  const config = ref<ConfigDTO | null>(null);
  const loaded = ref(false);
  const loadError = ref<string | null>(null);

  async function load(): Promise<void> {
    try {
      config.value = await api.getConfig();
      loadError.value = null;
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    } finally {
      loaded.value = true;
    }
  }

  /** Все model id `provider:model` из конфига (для палитры и валидации /model). */
  function allModelIds(): string[] {
    const c = config.value;
    if (c === null) return [];
    const ids: string[] = [];
    for (const [provider, p] of Object.entries(c.providers)) {
      for (const model of Object.keys(p.models)) ids.push(`${provider}:${model}`);
    }
    return ids;
  }

  function contextWindowFor(model: string): number {
    const c = config.value;
    if (c === null) return 0;
    const [provider, ...rest] = model.split(":");
    const m = rest.join(":");
    return c.providers[provider]?.models[m]?.context_window ?? c.context_window_default;
  }

  return { config, loaded, loadError, load, allModelIds, contextWindowFor };
});
