<script setup lang="ts">
import { computed, ref } from "vue";
import { NTag } from "naive-ui";
import MarkdownIt from "markdown-it";
import DOMPurify from "dompurify";
import type { MessageDTO } from "@/api/types";

const props = defineProps<{ message: MessageDTO; live?: boolean }>();

// действия под сообщением — не в выпадающем меню
const emit = defineEmits<{ action: [id: string] }>();

/** инлайн-иконки (SVG-пути, stroke) для кнопок действий */
const ICONS: Record<string, string[]> = {
  branch: [
    "M6 3a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "M18 15a3 3 0 1 0 0 6 3 3 0 0 0 0-6z",
    "M6 9v3a4 4 0 0 0 4 4h2",
    "M18 21v-3a4 4 0 0 0-4-4",
  ],
  remember: ["M12 2l2.9 6.26 6.6.57-5 4.4 1.5 6.47L12 16.7 5.99 19.7l1.5-6.47-5-4.4 6.6-.57z"],
  copy: ["M9 9h12v12H9z", "M5 15V5a2 2 0 0 1 2-2h10"],
};

const md = new MarkdownIt({ html: false, linkify: true, breaks: true });

const isAssistant = computed(() => props.message.role === "assistant");
const isUser = computed(() => props.message.role === "user");
const isTool = computed(() => props.message.role === "tool");
const isError = computed(() => props.message.error != null);

/** кнопки действий реплики — только для диалога, без system-ноток и ошибок */
const actions = computed(() => {
  if (isError.value) return [];
  const list: { id: string; label: string; icon: string[] }[] = [];
  if (isAssistant.value || isUser.value) {
    list.push({ id: "branch", label: "Ветка отсюда", icon: ICONS.branch });
  }
  if (isUser.value) {
    list.push({ id: "remember", label: "Запомнить", icon: ICONS.remember });
  }
  if (isAssistant.value || isUser.value) {
    list.push({ id: "copy", label: "Копировать", icon: ICONS.copy });
  }
  return list;
});

function onAction(id: string): void {
  emit("action", id);
}

// --- блок размышлений (thinking-модель) ---
const reasoningExpanded = ref(false);
const reasonText = computed(() => props.message.reasoning ?? "");
const reasonLines = computed(() => (reasonText.value === "" ? [] : reasonText.value.split("\n")));
const reasoningTruncated = computed(() => reasonLines.value.length > 3);
/** свёрнуто: последние 3 строки живого стрима */
const reasoningPreview = computed(() =>
  reasoningTruncated.value ? reasonLines.value.slice(-3).join("\n") : reasonText.value,
);
/** спиннер, пока модель думает (контент ещё не начался) */
const thinking = computed(() => props.live === true && props.message.content === "");

function toggleReasoning(): void {
  reasoningExpanded.value = !reasoningExpanded.value;
}

/** результат инструмента: «🔧 имя: вывод» — полный текст переносится по строкам */
const toolExpanded = ref(false);
const toolFull = computed(() => {
  if (!isTool.value) return "";
  const name = props.message.tool_name ?? "инструмент";
  return `🔧 ${name}: ${props.message.content}`;
});
/** свёрнуто: первые 3 строки + многоточие; клик по сообщению раскрывает всё */
const toolLines = computed(() => (toolFull.value === "" ? [] : toolFull.value.split("\n")));
const toolTruncated = computed(() => toolLines.value.length > 3);
const toolPreview = computed(() =>
  toolTruncated.value ? `${toolLines.value.slice(0, 3).join("\n")}\n…` : toolFull.value,
);
function toggleTool(): void {
  if (toolTruncated.value) toolExpanded.value = !toolExpanded.value;
}

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
  let line = `in ${approx ? "~" : ""}${u.prompt_tokens ?? 0} · out ${approx ? "~" : ""}${u.completion_tokens ?? 0}`;
  if (u.reasoning_tokens !== undefined && u.reasoning_tokens !== 0) {
    line += ` · think ${u.reasoning_tokens}`;
  }
  return line;
});
</script>

<template>
  <div class="msg" :class="`msg-${message.role}`">
    <div class="msg-main">
      <div v-if="isError" class="msg-error">{{ message.content }}</div>
      <template v-else-if="isAssistant">
        <div v-if="calledTools != null && calledTools.length > 0" class="msg-note msg-tool">
          🔧 вызвал: {{ calledTools.join(", ") }}
        </div>
        <div v-if="reasonText" class="msg-reasoning" :class="{ expanded: reasoningExpanded }" @click="toggleReasoning">
          <div class="msg-reasoning-head">
            <span class="msg-reasoning-label">🤔 Размышления</span>
            <span v-if="thinking" class="msg-reasoning-spinner" />
            <span class="msg-reasoning-caret">{{ reasoningExpanded ? "▾" : "▸" }}</span>
          </div>
          <div v-show="reasoningExpanded" class="msg-reasoning-body">{{ reasonText }}</div>
          <div v-show="!reasoningExpanded" class="msg-reasoning-preview" :class="{ live }">{{ reasoningPreview }}</div>
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
        <div class="msg-note msg-tool" :class="{ collapsed: toolTruncated }" @click="toggleTool">
          <span v-if="toolTruncated" class="msg-tool-caret">{{ toolExpanded ? "▾" : "▸" }}</span>
          <span v-if="!toolExpanded" class="msg-tool-body">{{ toolPreview }}</span>
          <span v-else class="msg-tool-body">{{ toolFull }}</span>
        </div>
      </template>
      <div v-else class="msg-note">{{ message.content }}</div>
      <div v-if="actions.length > 0" class="msg-actions">
        <button
          v-for="a in actions"
          :key="a.id"
          class="msg-action"
          type="button"
          @click="onAction(a.id)"
        >
          <svg class="msg-action-icon" viewBox="0 0 24 24" aria-hidden="true">
            <path v-for="d in a.icon" :key="d" :d="d" />
          </svg>
          <span>{{ a.label }}</span>
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.msg-main {
  position: relative;
}
.msg-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  margin-top: 6px;
  opacity: 0.55;
}
.msg:hover .msg-actions,
.msg-actions:hover {
  opacity: 1;
}
.msg-action {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 2px 7px;
  border: 0;
  border-radius: 4px;
  background: transparent;
  color: var(--n-text-color-3, #888);
  font-size: 12px;
  line-height: 1.4;
  cursor: pointer;
}
.msg-action:hover {
  background: var(--n-action-color, #333);
  color: var(--n-text-color, #ccc);
}
.msg-action-icon {
  width: 13px;
  height: 13px;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}
.msg-tool {
  opacity: 0.75;
  font-size: 12px;
  white-space: pre-wrap;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.msg-tool.collapsed {
  cursor: pointer;
}
.msg-tool-caret {
  margin-right: 4px;
  opacity: 0.6;
}
.msg-reasoning {
  margin-bottom: 6px;
  padding: 6px 8px;
  border-left: 2px solid var(--n-divider-color, #3b4261);
  border-radius: 4px;
  background: color-mix(in srgb, var(--n-fill-color, #1f2335) 45%, transparent);
  cursor: pointer;
  opacity: 0.9;
}
.msg-reasoning-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  font-weight: 600;
  color: var(--n-text-color-3, #9aa5ce);
}
.msg-reasoning-label {
  flex: 1;
}
.msg-reasoning-caret {
  font-size: 10px;
  opacity: 0.6;
}
.msg-reasoning-preview,
.msg-reasoning-body {
  margin-top: 4px;
  font-size: 12px;
  line-height: 1.5;
  color: var(--n-text-color-3, #787c99);
  white-space: pre-wrap;
}
.msg-reasoning-body {
  max-height: 320px;
  overflow: auto;
}
/* во время стрима блок фиксированной высоты: текст «прокручивается» внутри,
   а не меняет высоту сообщения (feedback: стрим размышлений) */
.msg-reasoning-preview.live {
  height: 4.5em; /* 3 строки (line-height 1.5 × font-size 12px) */
  overflow: hidden;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
}
.msg-reasoning-spinner {
  width: 10px;
  height: 10px;
  border: 2px solid transparent;
  border-top-color: var(--n-text-color-3, #787c99);
  border-radius: 50%;
  animation: msg-reasoning-spin 0.8s linear infinite;
}
@keyframes msg-reasoning-spin {
  to {
    transform: rotate(360deg);
  }
}
</style>
