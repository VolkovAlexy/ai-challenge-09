<script setup lang="ts">
import { computed, nextTick, ref } from "vue";
import { NButton } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";
import MessageItem from "./MessageItem.vue";
import MessageContextMenu, { type MenuItem } from "./MessageContextMenu.vue";
import type { MessageDTO } from "@/api/types";

const store = useAgentsStore();

const agent = computed(() => store.activeAgent);
const history = computed(() => agent.value?.history ?? []);

const container = ref<HTMLElement | null>(null);
const atBottom = ref(true);

// --- контекстное меню сообщения ---
const menu = ref<{ index: number; x: number; y: number } | null>(null);

const menuItems = computed<MenuItem[]>(() => {
  if (menu.value === null) return [];
  const message: MessageDTO | undefined = history.value[menu.value.index];
  if (message === undefined) return [];
  const items: MenuItem[] = [];
  if (message.role === "user" || message.role === "assistant") {
    items.push({ id: "branch", label: "Ветка отсюда" });
  }
  if (message.role === "user") {
    items.push({ id: "remember", label: "Запомнить (долговременная память)" });
  }
  items.push({ id: "copy", label: "Копировать текст" });
  return items;
});

function onMenu(index: number, pos: { x: number; y: number }): void {
  menu.value = { index, x: pos.x, y: pos.y };
}

async function onMenuSelect(action: string): Promise<void> {
  if (menu.value === null) return;
  const index = menu.value.index;
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
      <MessageItem :message="m" @menu="(pos) => onMenu(i, pos)" />
      <div
        v-if="i === history.length - 1 && agent?.compactionNote"
        class="msg-note compaction"
      >
        {{ agent.compactionNote }}
      </div>
    </template>
    <div v-if="agent?.compacting" class="loader">сжимаю контекст…</div>
    <div v-else-if="agent?.streaming && history.at(-1)?.role !== 'assistant'" class="loader">
      …думаю
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
    <MessageContextMenu
      v-if="menu !== null"
      :x="menu.x"
      :y="menu.y"
      :items="menuItems"
      @select="onMenuSelect"
      @close="menu = null"
    />
  </div>
</template>
