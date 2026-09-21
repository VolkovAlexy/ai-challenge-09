<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  NConfigProvider,
  NMessageProvider,
  NDialogProvider,
  NLayout,
  NLayoutContent,
  NLayoutSider,
  NModal,
  darkTheme,
  ruRU,
} from "naive-ui";
import { api } from "@/api/client";
import type { CommandDTO, PatchAgentDTO } from "@/api/types";
import { useAgentsStore } from "@/stores/agents";
import { useConfigStore } from "@/stores/config";
import { useSessionsStore } from "@/stores/sessions";
import { buildRegistry, findCommand, parseCommand, type ChatCommand, type CommandContext } from "@/commands/registry";
import { useChatStream } from "@/composables/useChatStream";
import ChatView from "@/components/ChatView.vue";
import SessionSidebar from "@/components/SessionSidebar.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import HistoryModal from "@/components/chat/HistoryModal.vue";
import HelpPalette from "@/components/palettes/HelpPalette.vue";
import ModelPalette from "@/components/palettes/ModelPalette.vue";
import SessionPalette from "@/components/palettes/SessionPalette.vue";

const agentsStore = useAgentsStore();
const configStore = useConfigStore();
const sessionsStore = useSessionsStore();

const commandsDTO = ref<CommandDTO[]>([]);
const registry = ref<ChatCommand[]>([]);
const bootError = ref<string | null>(null);

const themeOverrides = {
  common: {
    primaryColor: "#7aa2f7",
    primaryColorHover: "#8fb4f9",
    primaryColorPressed: "#6389dd",
    primaryColorSuppl: "#7aa2f7",
    bodyColor: "#101014",
    cardColor: "#1a1d25",
    modalColor: "#1a1d25",
    popoverColor: "#1a1d25",
    inputColor: "#1c212c",
    borderColor: "#2a2e3a",
    dividerColor: "#2a2e3a",
  },
  Layout: {
    siderColor: "#16171d",
    siderBorderColor: "#1e2030",
    contentColor: "#101014",
  },
  Select: {
    peers: {
      InternalSelection: {
        color: "#1c212c",
        border: "1px solid #2a2e3a",
      },
    },
  },
};

type Overlay =
  | { kind: "none" }
  | { kind: "model" }
  | { kind: "session" }
  | { kind: "help" }
  | { kind: "history" }
  | { kind: "system-prompt" }
  | { kind: "confirm"; text: string; action: "close" | "clear" | "delete-session" };
const overlay = ref<Overlay>({ kind: "none" });

function onStreamEvent(ev: { event: string }): void {
  if (ev.event === "done" || ev.event === "cancelled" || ev.event === "error") {
    void sessionsStore.load(20, 0);
  }
}

const stream = useChatStream(
  () => agentsStore.activeAgentId,
  onStreamEvent,
);

async function boot(): Promise<void> {
  await configStore.load();
  try {
    const cmds = await api.getCommands();
    commandsDTO.value = cmds;
    registry.value = buildRegistry(cmds);
  } catch (e) {
    bootError.value = e instanceof Error ? e.message : String(e);
  }
  try {
    await agentsStore.loadAll();
  } catch (e) {
    bootError.value = e instanceof Error ? e.message : String(e);
  }
  if (agentsStore.activeAgent === null) {
    await agentsStore.createAgent();
  }
}

onMounted(boot);

function cmdContext(): CommandContext {
  return {
    requests: {
      help: () => { overlay.value = { kind: "help" }; },
      model: () => { overlay.value = { kind: "model" }; },
      session: () => { overlay.value = { kind: "session" }; },
      history: () => { overlay.value = { kind: "history" }; },
      systemPrompt: () => { overlay.value = { kind: "system-prompt" }; },
      note: (text) => {
        const id = agentsStore.activeAgentId;
        const state = id !== null ? agentsStore.agents[id] : undefined;
        if (state !== undefined) {
          state.history.push({ id: `note-${Date.now()}`, role: "system", content: text });
        }
      },
    },
    actions: {
      newAgent: async (name) => { await agentsStore.createAgent(name); },
      closeAgent: async () => {
        const id = agentsStore.activeAgentId;
        if (id === null) return;
        const streaming = agentsStore.agents[id]?.streaming === true;
        if (streaming) {
          const ok = await confirmAsk("Есть незавершённый запрос. Закрыть агента?", "close");
          if (!ok) return;
        }
        await agentsStore.closeAgent(id);
      },
      rename: async (name) => { await patchActive({ name }); },
      setModel: async (model) => {
        if (configStore.config !== null && !configStore.allModelIds().includes(model)) {
          throw new Error(`Неизвестная модель: ${model}. Доступны: ${configStore.allModelIds().join(", ")}`);
        }
        await patchActive({ model });
      },
      setTemperature: async (v) => { await patchActive({ temperature: v }); },
      setTopP: async (v) => { await patchActive({ top_p: v }); },
      setMaxTokens: async (n) => { await patchActive({ max_tokens: n }); },
      setStop: async (seqs) => { await patchActive({ stop: seqs }); },
      setSystemPromptPath: async (path) => {
        await api.putSystemPrompt(path);
        await patchActive({ system_prompt_path: path });
      },
      clearHistory: async () => {
        const id = agentsStore.activeAgentId;
        if (id !== null) await agentsStore.clearHistory(id);
      },
      loadSession: async (sessionId) => {
        const id = agentsStore.activeAgentId;
        if (id !== null) await agentsStore.loadSession(id, sessionId);
      },
      exportSession: async (path) => {
        const id = agentsStore.activeAgentId;
        if (id === null) return "Нет активного агента.";
        const res = await api.exportSession(id, path);
        return `Экспортировано: ${res.path}`;
      },
    },
    modelIds: () => configStore.allModelIds(),
    currentModel: () => agentsStore.activeAgent?.model ?? null,
  };
}

async function patchActive(patch: PatchAgentDTO): Promise<void> {
  const id = agentsStore.activeAgentId;
  if (id === null) return;
  try {
    await agentsStore.patchAgent(id, patch);
  } catch (e) {
    agentsStore.agents[id]?.history.push({
      id: `patch-err-${Date.now()}`,
      role: "system",
      content: e instanceof Error ? e.message : String(e),
      error: { kind: "http", detail: e instanceof Error ? e.message : String(e) },
    });
  }
}

let confirmResolve: ((ok: boolean) => void) | null = null;
function confirmAsk(text: string, action: "close" | "clear" | "delete-session"): Promise<boolean> {
  overlay.value = { kind: "confirm", text, action };
  return new Promise((resolve) => { confirmResolve = resolve; });
}

function onConfirmAnswer(ok: boolean): void {
  const ov = overlay.value;
  overlay.value = { kind: "none" };
  confirmResolve?.(ok);
  confirmResolve = null;
  if (ok && ov.kind === "confirm" && ov.action === "close") {
    const id = agentsStore.activeAgentId;
    if (id !== null) void agentsStore.closeAgent(id);
  }
}

async function onSend(text: string): Promise<void> {
  const parsed = parseCommand(text);
  if (parsed !== null) {
    const cmd = findCommand(registry.value, parsed.cmd);
    if (cmd === undefined) {
      const id = agentsStore.activeAgentId;
      const state = id !== null ? agentsStore.agents[id] : undefined;
      const known = registry.value.map((c) => `/${c.name}`).join(" ");
      state?.history.push({
        id: `unknown-${Date.now()}`,
        role: "system",
        content: `Неизвестная команда: /${parsed.cmd}. Доступные команды: ${known}`,
      });
      return;
    }
    const ctx = cmdContext();
    try {
      const note = await cmd.run(ctx, parsed.args);
      if (typeof note === "string" && note !== "") ctx.requests.note(note);
    } catch (e) {
      ctx.requests.note(`Ошибка: ${e instanceof Error ? e.message : String(e)}`);
    }
    return;
  }
  await stream.send(text);
}

async function onStop(): Promise<void> {
  await stream.cancel();
}

const modelIds = () => configStore.allModelIds();

function onModelChange(model: string): void {
  void patchActive({ model });
}

function onPaletteSelect(value: string | null): void {
  const kind = overlay.value.kind;
  overlay.value = { kind: "none" };
  if (kind === "model" && typeof value === "string") {
    void patchActive({ model: value });
  } else if (kind === "session" && typeof value === "string") {
    const id = agentsStore.activeAgentId;
if (id !== null) void agentsStore.loadSession(id, value);
  }
}

function onHelpSelect(cmd: string | null): void {
  overlay.value = { kind: "none" };
  if (cmd !== null) {
    const state = agentsStore.activeAgent;
    state?.history.push({ id: `help-${Date.now()}`, role: "system", content: `Введите ${cmd} …` });
  }
}

function onSessionSelect(sessionId: string): void {
  const id = agentsStore.activeAgentId;
  if (id !== null) void agentsStore.loadSession(id, sessionId);
}

async function onSessionBranch(sessionId: string): Promise<void> {
  await agentsStore.branchFromSession(sessionId);
  await sessionsStore.reload();
}

async function onSessionRename(sessionId: string, title: string): Promise<void> {
  await sessionsStore.rename(sessionId, title);
}

async function onSessionDelete(sessionId: string): Promise<void> {
  const ok = await confirmAsk("Удалить сессию? Действие необратимо.", "delete-session");
  if (!ok) return;
  await sessionsStore.remove(sessionId);
}

async function onNewChat(): Promise<void> {
  await agentsStore.createAgent();
}
</script>

<template>
  <n-config-provider :theme="darkTheme" :theme-overrides="themeOverrides" :locale="ruRU">
    <n-message-provider>
      <n-dialog-provider>
        <n-layout has-sider class="app-layout">
          <n-layout-sider
            bordered
            :width="260"
            :native-scrollbar="false"
            collapse-mode="width"
          >
            <SessionSidebar
              :active-session-id="agentsStore.activeAgent?.sessionId ?? null"
              @select="onSessionSelect"
              @new-chat="onNewChat"
              @branch="onSessionBranch"
              @delete="onSessionDelete"
              @rename="onSessionRename"
            />
          </n-layout-sider>
          <n-layout-content>
            <main class="app-main">
              <div v-if="bootError !== null" class="boot-error" style="padding: 6px 16px; background: #3b2630; color: #f7768e; font-size: 13px;">
                Бэкенд недоступен: {{ bootError }}
              </div>
              <template v-if="agentsStore.activeAgent !== null">
                <ChatView
                  :commands="registry"
                  :model-ids="modelIds"
                  :current-model="agentsStore.activeAgent?.model ?? null"
                  :on-send="onSend"
                  :on-stop="onStop"
                  :on-model-change="onModelChange"
                />
              </template>
            </main>
          </n-layout-content>
        </n-layout>

        <!-- Palettes & Modals -->
        <ModelPalette
          v-if="overlay.kind === 'model'"
          :current="agentsStore.activeAgent?.model ?? null"
          @select="onPaletteSelect"
        />
        <HelpPalette
          v-if="overlay.kind === 'help'"
          :commands="registry"
          @select="onHelpSelect"
        />
        <SessionPalette
          v-if="overlay.kind === 'session'"
          @select="(v) => { overlay = { kind: 'none' }; if (v) onSessionSelect(v); }"
        />
        <HistoryModal
          v-if="overlay.kind === 'history'"
          @close="overlay = { kind: 'none' }"
        />
        <n-modal
          v-if="overlay.kind === 'system-prompt'"
          :show="true"
          preset="card"
          title="Системный промпт"
          style="width: 680px;"
          @close="overlay = { kind: 'none' }"
        >
          <div style="color: #888; font-size: 12px; margin-bottom: 8px;">
            {{ agentsStore.activeAgent?.systemPromptPath }}
          </div>
          <pre style="margin: 0; white-space: pre-wrap; word-break: break-word; font-family: var(--mono); font-size: 13px; max-height: 60vh; overflow: auto; color: #c8ccd4;">{{ agentsStore.activeAgent?.systemPromptContent }}</pre>
        </n-modal>
        <ConfirmDialog
          v-if="overlay.kind === 'confirm'"
          :text="overlay.text"
          @answer="onConfirmAnswer"
        />
      </n-dialog-provider>
    </n-message-provider>
  </n-config-provider>
</template>