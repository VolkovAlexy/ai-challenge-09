<script setup lang="ts">
// Баннер предложения памяти: агент нашёл знание, достойное долговременной памяти.
// Принять — текст уходит в LONGTERM_MEMORY.md; Отклонить — предложение снимается.
import { computed, ref } from "vue";
import { NButton, useMessage } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";

const store = useAgentsStore();
const message = useMessage();

const agent = computed(() => store.activeAgent);
const suggestion = computed(() => agent.value?.memorySuggestion ?? null);
const busy = ref(false);

async function accept(): Promise<void> {
  if (agent.value === null || busy.value) return;
  busy.value = true;
  try {
    await store.acceptSuggestion(agent.value.id);
    message.success("Знание сохранено в долговременную память");
  } catch (e) {
    message.error(e instanceof Error ? e.message : "не удалось сохранить");
  } finally {
    busy.value = false;
  }
}

async function dismiss(): Promise<void> {
  if (agent.value === null) return;
  await store.dismissSuggestion(agent.value.id);
}
</script>

<template>
  <div v-if="suggestion !== null" class="suggestion">
    <div class="suggestion-text">
      <span class="suggestion-title">Предлагаю запомнить:</span> {{ suggestion }}
    </div>
    <div class="suggestion-actions">
      <n-button size="tiny" type="primary" :loading="busy" @click="accept">Принять</n-button>
      <n-button size="tiny" quaternary @click="dismiss">Отклонить</n-button>
    </div>
  </div>
</template>

<style scoped>
.suggestion {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 16px;
  border-top: 1px solid var(--n-border-color, #3a3a3f);
  background: var(--n-action-color, #2a2a2e);
  font-size: 13px;
}
.suggestion-text {
  flex: 1;
  min-width: 0;
}
.suggestion-title {
  color: var(--n-text-color-3, #999);
}
.suggestion-actions {
  display: flex;
  gap: 8px;
  flex-shrink: 0;
}
</style>
