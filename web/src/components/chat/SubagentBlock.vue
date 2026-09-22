<script setup lang="ts">
import { computed, ref } from "vue";
import type { SubagentBlock } from "@/stores/agents";

const props = defineProps<{ block: SubagentBlock }>();

const expanded = ref(false);
const lines = computed(() => (props.block.text === "" ? [] : props.block.text.split("\n")));
const truncated = computed(() => lines.value.length > 3);
const preview = computed(() =>
  truncated.value ? lines.value.slice(-3).join("\n") : props.block.text,
);
/** статус субагента в заголовке — чтобы было понятно, что происходит */
const busy = computed(() => !props.block.done);
const status = computed(() => {
  if (props.block.done) return "готово";
  return props.block.text === "" ? "думает…" : "в работе…";
});

function toggle(): void {
  expanded.value = !expanded.value;
}
</script>

<template>
  <div class="subagent" :class="{ expanded }" @click="toggle">
    <div class="subagent-head">
      <span class="subagent-label">🧩 Субагент: {{ block.profile }}</span>
      <span v-if="busy" class="subagent-status">{{ status }}</span>
      <span v-if="busy" class="subagent-spinner" />
      <span v-else class="subagent-done">✓</span>
      <span class="subagent-caret">{{ expanded ? "▾" : "▸" }}</span>
    </div>
    <div v-show="expanded" class="subagent-body">{{ block.text }}</div>
    <div v-show="!expanded && block.text" class="subagent-preview" :class="{ live: busy }">{{ preview }}</div>
  </div>
</template>

<style scoped>
.subagent {
  margin-bottom: 6px;
  padding: 6px 8px;
  border-left: 2px solid var(--n-divider-color, #4a3b63);
  border-radius: 4px;
  background: color-mix(in srgb, var(--n-fill-color, #221f33) 45%, transparent);
  cursor: pointer;
  opacity: 0.9;
}
.subagent-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--n-text-color-3, #b39ddb);
}
.subagent-label {
  flex: 1;
}
.subagent-status {
  font-size: 11px;
  font-weight: 400;
  color: var(--n-text-color-3, #9aa5ce);
  opacity: 0.85;
}
.subagent-done {
  font-size: 11px;
  color: #9ece6a;
}
.subagent-caret {
  font-size: 10px;
  opacity: 0.6;
}
.subagent-preview,
.subagent-body {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--n-text-color-3, #787c99);
  white-space: pre-wrap;
}
.subagent-body {
  max-height: 320px;
  overflow: auto;
}
/* во время стрима размышлений блок фиксированной высоты: текст «прокручивается»
   внутри (last lines), а не меняет высоту всего сообщения */
.subagent-preview.live {
  height: 4.5em;
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}
.subagent-spinner {
  width: 10px;
  height: 10px;
  border: 2px solid transparent;
  border-top-color: var(--n-text-color-3, #b39ddb);
  border-radius: 50%;
  animation: subagent-spin 0.8s linear infinite;
}
@keyframes subagent-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
