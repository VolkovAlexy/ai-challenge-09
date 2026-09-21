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

const statusText = computed(() => {
  const a = agent.value;
  if (a === null) return "";
  if (a.compacting) return "сжимаю контекст…";
  if (a.streaming) return a.cancelled ? "останавливаю…" : "стрим…";
  return "";
});
</script>

<template>
  <div v-if="agent" class="statusbar">
    <div class="st-left">
      <span v-if="statusText !== ''" class="st-status">{{ statusText }}</span>
      <template v-else>
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
      </template>
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