<script setup lang="ts">
import { computed } from "vue";
import { NModal, NButton, NSpace } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";

const emit = defineEmits<{ close: [] }>();

const store = useAgentsStore();

const text = computed(() => {
  const a = store.activeAgent;
  if (a === null) return "";
  const lines: string[] = [];
  for (const m of a.history) {
    lines.push(`[${m.role}] ${m.content}`);
    if (m.reasoning !== undefined && m.reasoning !== "") lines.push(`[think] ${m.reasoning}`);
  }
  return lines.join("\n\n");
});

async function copy(): Promise<void> {
  await navigator.clipboard.writeText(text.value);
}
</script>

<template>
  <n-modal
    :show="true"
    preset="card"
    title="История диалога"
    style="width: 720px;"
    @close="emit('close')"
  >
    <template #header-extra>
      <n-space>
        <n-button size="small" @click="copy">Копировать</n-button>
        <n-button size="small" @click="emit('close')">Закрыть</n-button>
      </n-space>
    </template>
    <pre class="history-body">{{ text }}</pre>
  </n-modal>
</template>

<style scoped>
.history-body {
  margin: 0;
  padding: 12px;
  overflow: auto;
  max-height: 60vh;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  font-size: 13px;
  color: #c8ccd4;
  background: #11131a;
  border-radius: 8px;
}
</style>