<script setup lang="ts">
import { computed } from "vue";
import { NTag } from "naive-ui";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import type { MessageDTO } from "@/api/types";

const props = defineProps<{ message: MessageDTO }>();

// меню действий: только для реплик диалога (system-нотки без действий)
const emit = defineEmits<{ menu: [pos: { x: number; y: number }] }>();

const md = new MarkdownIt({ html: false, linkify: true, breaks: true });

const isAssistant = computed(() => props.message.role === "assistant");
const isUser = computed(() => props.message.role === "user");
const isTool = computed(() => props.message.role === "tool");
const isError = computed(() => props.message.error != null);
const hasMenu = computed(() => (isAssistant.value || isUser.value) && !isError.value);

/** результат инструмента: «🔧 имя: вывод» (обрезан, полный — в title) */
const toolLine = computed(() => {
  if (!isTool.value) return null;
  const name = props.message.tool_name ?? "инструмент";
  const output = props.message.content;
  const preview = output.length > 120 ? output.slice(0, 120) + "…" : output;
  return { name, preview, full: output };
});

/** имена инструментов, вызванных ассистентом (для бейджа) */
const calledTools = computed(() => props.message.tool_calls ?? null);

const rendered = computed(() => {
  if (!isAssistant.value) return "";
  return DOMPurify.sanitize(md.render(props.message.content));
});

const userEscaped = computed(() => {
  if (!isUser.value) return "";
  const div = document.createElement("div");
  div.textContent = props.message.content;
  return div.innerHTML;
});

const tokensLine = computed(() => {
  const u = props.message.usage;
  if (u == null) return null;
  const approx = u.approx === true;
  return `in ${approx ? "~" : ""}${u.prompt_tokens ?? 0} · out ${approx ? "~" : ""}${u.completion_tokens ?? 0}`;
});

function onMenuClick(event: MouseEvent): void {
  const button = event.currentTarget as HTMLElement;
  const rect = button.getBoundingClientRect();
  emit("menu", { x: rect.right + 6, y: rect.top });
}
</script>

<template>
  <div class="msg" :class="`msg-${message.role}`">
    <div class="msg-main">
      <div v-if="isError" class="msg-error">{{ message.content }}</div>
      <template v-else-if="isAssistant">
        <div v-if="calledTools != null && calledTools.length > 0" class="msg-note msg-tool">
          🔧 вызвал: {{ calledTools.join(", ") }}
        </div>
        <div class="msg-body md" v-html="rendered" />
        <div v-if="tokensLine" class="msg-tokens">
          <n-tag size="tiny" :bordered="false">{{ tokensLine }}</n-tag>
        </div>
      </template>
      <template v-else-if="isUser">
        <div class="msg-body" v-html="userEscaped" />
      </template>
      <template v-else-if="isTool">
        <div class="msg-note msg-tool" :title="toolLine?.full">
          🔧 {{ toolLine?.name }}: {{ toolLine?.preview }}
        </div>
      </template>
      <div v-else class="msg-note">{{ message.content }}</div>
      <button
        v-if="hasMenu"
        class="msg-menu-btn"
        type="button"
        title="Действия с сообщением"
        @click.stop="onMenuClick"
      >
        ⋮
      </button>
    </div>
  </div>
</template>

<style scoped>
.msg-main {
  position: relative;
}
.msg-menu-btn {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 22px;
  height: 22px;
  display: none;
  align-items: center;
  justify-content: center;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: var(--n-text-color-3, #888);
  font-size: 15px;
  line-height: 1;
  cursor: pointer;
  opacity: 0.7;
}
.msg:hover .msg-menu-btn {
  display: flex;
  opacity: 1;
}
.msg-menu-btn:hover {
  background: var(--n-action-color, #333);
  opacity: 1;
}
.msg-tool {
  opacity: 0.75;
  font-size: 12px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
