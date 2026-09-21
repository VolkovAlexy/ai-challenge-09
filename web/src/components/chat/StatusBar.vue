<script setup lang="ts">
import { computed } from "vue";
import { NProgress } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";
import { useConfigStore } from "@/stores/config";

const store = useAgentsStore();
const configStore = useConfigStore();

const agent = computed(() => store.activeAgent);

const pct = computed(() => {
  const a = agent.value;
  if (a === null || a.contextWindow === 0) return 0;
  return a.contextUsed / a.contextWindow;
});

const threshold = computed(() => configStore.config?.compaction_threshold ?? 0.6);

const overThreshold = computed(() => pct.value > threshold.value);

/** спиннер во время стрима — поверх информации о контексте, без слова «стрим» */
const showSpinner = computed(
  () =>
    agent.value !== null &&
    agent.value.streaming &&
    !agent.value.cancelled &&
    !agent.value.compacting,
);

const statusText = computed(() => {
  const a = agent.value;
  if (a === null) return "";
  if (a.compacting) return "сжимаю контекст…";
  if (a.streaming && a.cancelled) return "останавливаю…";
  return "";
});
</script>

<template>
  <div v-if="agent" class="statusbar">
    <div class="st-left">
      <span v-if="showSpinner" class="st-spinner" />
      <span v-if="statusText !== ''" class="st-status">{{ statusText }}</span>
      <span>контекст</span>
      <span class="st-context" :class="{ warn: overThreshold }">
        {{ agent.contextUsed }}/{{ agent.contextWindow }}
      </span>
      <n-progress
        type="line"
        :percentage="Math.round(pct * 100)"
        :color="overThreshold ? '#e0af68' : '#7aa2f7'"
        :height="4"
        :border-radius="2"
        :show-indicator="false"
        style="width: 60px; min-width: 40px;"
      />
    </div>
    <slot />
    <div class="st-right">
      <span>in {{ agent.tokensIn }}</span>
      <span class="st-sep">·</span>
      <span>out {{ agent.tokensOut }}</span>
      <span class="st-sep">·</span>
      <span>T {{ agent.settings.temperature }}</span>
    </div>
  </div>
</template>

<style scoped>
.st-spinner {
  display: inline-block;
  width: 12px;
  height: 12px;
  margin-right: 6px;
  border: 2px solid transparent;
  border-top-color: currentColor;
  border-radius: 50%;
  animation: statusbar-spin 0.8s linear infinite;
  vertical-align: -2px;
}
@keyframes statusbar-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>