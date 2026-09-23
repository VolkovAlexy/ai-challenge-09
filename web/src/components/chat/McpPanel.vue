<script setup lang="ts">
// Панель MCP: все сконфигурированные серверы, их статус (доступен/нет) и
// глобальный вкл/выкл. Статус и вкл/выкл живут в сторе (оперативка бэкенда).
// Клик по карточке раскрывает список инструментов этого сервера.
import { computed, ref, watch } from "vue";
import { NSwitch, NTag } from "naive-ui";
import { useMcpStore } from "@/stores/mcp";

const props = defineProps<{ active: boolean }>();

const store = useMcpStore();
const servers = computed(() => store.servers);
const error = computed(() => store.loadError);

watch(
  () => props.active,
  (active) => {
    if (active) void store.load();
  },
  { immediate: true },
);

const STATUS_TEXT: Record<string, string> = {
  connecting: "подключение…",
  available: "доступен",
  unavailable: "недоступен",
};
const STATUS_COLOR: Record<string, string> = {
  connecting: "#e5c07b",
  available: "#98c379",
  unavailable: "#e06c75",
};

/** раскрытые карточки (по имени сервера) */
const expanded = ref(new Set<string>());

function toggleExpand(name: string): void {
  const next = new Set(expanded.value);
  if (next.has(name)) {
    next.delete(name);
  } else {
    next.add(name);
  }
  expanded.value = next;
}

function onToggle(name: string, enabled: boolean): void {
  void store.toggle(name, enabled);
}
</script>

<template>
  <div class="mcp-panel">
    <p class="hint">
      Внешние инструменты (MCP-серверы). Подключение происходит на старте; выключенный
      сервер не даёт своих инструментов модели. Инструменты от включённых и доступных
      серверов добавляются агенту автоматически. Клик по карточке — показать инструменты.
    </p>
    <div v-if="error" class="mcp-error">{{ error }}</div>
    <div v-else-if="!store.loaded" class="mcp-empty">загрузка…</div>
    <div v-else-if="servers.length === 0" class="mcp-empty">
      MCP-серверы не настроены — добавьте блок <code>mcp_servers</code> в config.json.
    </div>
    <ul v-else class="mcp-list">
      <li
        v-for="s in servers"
        :key="s.name"
        class="mcp-item"
        @click="toggleExpand(s.name)"
      >
        <div class="mcp-header">
          <span class="mcp-dot" :style="{ background: STATUS_COLOR[s.status] }" />
          <span class="mcp-name" :title="s.name">{{ s.name }}</span>
          <n-tag size="tiny" :bordered="false" class="mcp-transport">{{ s.transport }}</n-tag>
          <span class="mcp-status">{{ STATUS_TEXT[s.status] }}</span>
          <span v-if="s.tool_count > 0" class="mcp-count">{{ s.tool_count }} инстр.</span>
          <n-switch
            size="small"
            class="mcp-switch"
            :value="s.enabled"
            :disabled="s.status === 'connecting'"
            @click.stop
            @update:value="(v: boolean) => onToggle(s.name, v)"
          />
          <span class="mcp-caret">{{ expanded.has(s.name) ? "▾" : "▸" }}</span>
        </div>
        <div v-if="expanded.has(s.name)" class="mcp-tools">
          <template v-if="(s.tools ?? []).length > 0">
            <div v-for="t in s.tools" :key="t.name" class="mcp-tool">
              <span class="mcp-tool-name">{{ t.name }}</span>
              <span v-if="t.description" class="mcp-tool-desc">{{ t.description }}</span>
            </div>
          </template>
          <div v-else class="mcp-tools-empty">нет инструментов</div>
        </div>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.mcp-panel {
  padding-top: 4px;
}
.hint {
  margin: 0 0 8px;
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  line-height: 1.5;
}
.mcp-list {
  list-style: none;
  margin: 8px 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mcp-item {
  border: 1px solid var(--n-border-color, #3a3a3f);
  border-radius: 6px;
  cursor: pointer;
}
.mcp-header {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 10px;
}
.mcp-item:hover .mcp-header {
  background: color-mix(in srgb, var(--n-fill-color, #1f2335) 40%, transparent);
}
.mcp-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.mcp-name {
  font-size: 13px;
  font-weight: 600;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.mcp-transport {
  flex: none;
}
.mcp-status {
  font-size: 12px;
  color: var(--n-text-color-disabled, #888);
  flex: none;
}
.mcp-count {
  font-size: 11px;
  color: var(--n-text-color-disabled, #888);
  flex: none;
}
.mcp-switch {
  margin-left: auto;
}
.mcp-caret {
  flex: none;
  font-size: 10px;
  opacity: 0.6;
}
.mcp-tools {
  padding: 2px 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.mcp-tool {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.mcp-tool-name {
  font-size: 12px;
  font-weight: 600;
  color: var(--n-text-color, #ccc);
  font-family: v-mono, ui-monospace, monospace;
}
.mcp-tool-desc {
  font-size: 11px;
  color: var(--n-text-color-disabled, #888);
  line-height: 1.4;
}
.mcp-tools-empty {
  font-size: 12px;
  color: var(--n-text-color-disabled, #666);
}
.mcp-error {
  color: #e05c5c;
  font-size: 12px;
  margin: 6px 0;
}
.mcp-empty {
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  padding: 8px 0;
}
.mcp-empty code {
  font-size: 11px;
}
</style>
