// MCP-серверы: список и глобальный вкл/выкл (хранится только в оперативке).
import { defineStore } from "pinia";
import { ref } from "vue";
import { api } from "@/api/client";
import type { McpDTO } from "@/api/types";

export const useMcpStore = defineStore("mcp", () => {
  const servers = ref<McpDTO[]>([]);
  const loaded = ref(false);
  const loadError = ref<string | null>(null);

  async function load(): Promise<void> {
    try {
      servers.value = await api.listMcp();
      loadError.value = null;
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    } finally {
      loaded.value = true;
    }
  }

  async function toggle(name: string, enabled: boolean): Promise<void> {
    try {
      servers.value = await api.setMcp(name, { enabled });
      loadError.value = null;
    } catch (e) {
      loadError.value = e instanceof Error ? e.message : String(e);
    }
  }

  return { servers, loaded, loadError, load, toggle };
});
