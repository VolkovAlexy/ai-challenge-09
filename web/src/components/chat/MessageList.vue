<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { NButton } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";
import MessageItem from "./MessageItem.vue";

const store = useAgentsStore();

const agent = computed(() => store.activeAgent);
const history = computed(() => agent.value?.history ?? []);

const container = ref<HTMLElement | null>(null);
const atBottom = ref(true);

// --- автоскролл вниз только когда пользователь у нижнего края (§7.3) ---
// Сигнатура последнего сообщения меняется на каждый апдейт стрима/размышлений.
const listSignature = computed(() => {
  const last = history.value[history.value.length - 1];
  return `${history.value.length}:${last?.content.length ?? 0}:${last?.reasoning?.length ?? 0}`;
});

watch(listSignature, () => {
  if (atBottom.value) {
    void nextTick(() => container.value?.scrollTo({ top: container.value.scrollHeight }));
  }
});

// --- действия под сообщением (branch/remember/copy) ---
async function onAction(index: number, action: string): Promise<void> {
  const message = history.value[index];
  const agentId = agent.value?.id;
  if (message === undefined || agentId === undefined) return;
  if (action === "copy") {
    await navigator.clipboard.writeText(message.content);
    return;
  }
  if (action === "remember") {
    await store.rememberMessage(message.content);
    note("Сохранено в долговременную память (LONGTERM_MEMORY.md)");
    return;
  }
  if (action === "branch") {
    try {
      await store.forkAt(agentId, index);
      note(`Ветка создана от сообщения #${index + 1}`);
    } catch (e) {
      note(e instanceof Error ? e.message : "не удалось создать ветку");
    }
  }
}

function note(text: string): void {
  const state = agent.value;
  if (state == null) return;
  state.history.push({ id: `note-${Date.now()}`, role: "system", content: text });
}

const EDGE_PX = 40;

function onScroll(): void {
  const el = container.value;
  if (el === null) return;
  const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
  const was = atBottom.value;
  atBottom.value = distance <= EDGE_PX;
  if (!was && atBottom.value) {
    void nextTick(() => el.scrollTo({ top: el.scrollHeight }));
  }
}

function scrollDown(): void {
  const el = container.value;
  if (el === null) return;
  el.scrollTo({ top: el.scrollHeight });
  atBottom.value = true;
}

defineExpose({ scrollDown });
</script>

<template>
  <div class="msglist" ref="container" @scroll.passive="onScroll">
    <div v-if="!agent || history.length === 0" class="msglist-empty">
      История пуста — напишите что-нибудь или введите /help
    </div>
    <template v-for="(m, i) in history" :key="m.id + i">
      <MessageItem
        :message="m"
        :live="i === history.length - 1 && agent?.streaming === true"
        @action="(action) => onAction(i, action)"
      />
      <div
        v-if="i === history.length - 1 && agent?.compactionNote"
        class="msg-note compaction"
      >
        {{ agent.compactionNote }}
      </div>
    </template>
    <div v-if="agent?.compacting" class="loader">сжимаю контекст…</div>
    <div v-else-if="agent?.streaming && history.at(-1)?.role !== 'assistant'" class="loader">
      <span class="loader-spinner" />
    </div>
    <n-button
      v-if="!atBottom"
      class="scroll-down"
      size="small"
      quaternary
      @click="scrollDown"
    >
      Вниз ↓
    </n-button>
  </div>
</template>

<style scoped>
.loader-spinner {
  display: inline-block;
  width: 14px;
  height: 14px;
  margin-right: 6px;
  border: 2px solid transparent;
  border-top-color: var(--n-primary-color, #7aa2f7);
  border-radius: 50%;
  animation: msglist-spin 0.8s linear infinite;
  vertical-align: -2px;
}
@keyframes msglist-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
