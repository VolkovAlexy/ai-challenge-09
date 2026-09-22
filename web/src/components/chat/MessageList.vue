<script setup lang="ts">
import { computed, nextTick, ref, watch } from "vue";
import { NButton } from "naive-ui";
import { useAgentsStore, type SubagentBlock as SubagentBlockT } from "@/stores/agents";
import type { MessageDTO } from "@/api/types";
import MessageItem from "./MessageItem.vue";
import SubagentBlock from "./SubagentBlock.vue";

type TimelineItem =
  | { kind: "msg"; msg: MessageDTO; idx: number }
  | { kind: "sub"; si: number; block: SubagentBlockT };

const store = useAgentsStore();

const agent = computed(() => store.activeAgent);
const history = computed(() => agent.value?.history ?? []);

const container = ref<HTMLElement | null>(null);
const atBottom = ref(true);

// --- хронологичная лента: сообщения и субагент-блоки в порядке появления (§7.2) ---
const timeline = computed<TimelineItem[]>(() => {
  const hist = history.value;
  const subs = agent.value?.subagents ?? [];
  const items: TimelineItem[] = [];
  let cursor = 0;
  for (let i = 0; i < hist.length; i++) {
    items.push({ kind: "msg", msg: hist[i], idx: i });
    while (cursor < subs.length && subs[cursor].anchor <= i + 1) {
      items.push({ kind: "sub", si: cursor, block: subs[cursor] });
      cursor++;
    }
  }
  // блоки, чей якорь ещё за пределами выгруженной истории — в конец
  while (cursor < subs.length) {
    items.push({ kind: "sub", si: cursor, block: subs[cursor] });
    cursor++;
  }
  return items;
});

// --- автоскролл вниз только когда пользователь у нижнего края (§7.3) ---
// Сигнатура последнего сообщения меняется на каждый апдейт стрима/размышлений.
const listSignature = computed(() => {
  const last = history.value[history.value.length - 1];
  const subs = agent.value?.subagents ?? [];
  const subSig = subs.reduce((acc, s) => acc + s.text.length, 0) + subs.length;
  return `${history.value.length}:${last?.content.length ?? 0}:${last?.reasoning?.length ?? 0}:${subSig}`;
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
    note("Сохранено в долговременную память");
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
    <template v-for="item in timeline" :key="item.kind === 'msg' ? `m-${item.msg.id}-${item.idx}` : `s-${item.block.profile}-${item.si}`">
      <template v-if="item.kind === 'msg'">
        <MessageItem
          :message="item.msg"
          :live="item.idx === history.length - 1 && agent?.streaming === true"
          @action="(action) => onAction(item.idx, action)"
        />
        <div
          v-if="item.idx === history.length - 1 && agent?.compactionNote"
          class="msg-note compaction"
        >
          {{ agent.compactionNote }}
        </div>
      </template>
      <SubagentBlock v-else :block="item.block" />
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
