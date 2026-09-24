<script setup lang="ts">
import { computed, onMounted, ref, watch } from "vue";
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
import { useMcpStore } from "@/stores/mcp";
import { useSessionsStore } from "@/stores/sessions";
import { useProjectsStore } from "@/stores/projects";
import { useProfilesStore } from "@/stores/profiles";
import { buildRegistry, findCommand, parseCommand, type ChatCommand, type CommandContext } from "@/commands/registry";
import { useChatStream } from "@/composables/useChatStream";
import ChatView from "@/components/ChatView.vue";
import SessionSidebar from "@/components/SessionSidebar.vue";
import ConfirmDialog from "@/components/common/ConfirmDialog.vue";
import ProjectMemoryModal from "@/components/ProjectMemoryModal.vue";

const agentsStore = useAgentsStore();
const configStore = useConfigStore();
const mcpStore = useMcpStore();
const sessionsStore = useSessionsStore();
const projectsStore = useProjectsStore();
const profilesStore = useProfilesStore();

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
  | { kind: "confirm"; text: string; action: "close" | "delete-session" | "delete-project" }
  | { kind: "project-memory"; projectId: string; projectName: string };
const overlay = ref<Overlay>({ kind: "none" });

function onStreamEvent(ev: { event: string }): void {
  if (ev.event === "done" || ev.event === "cancelled" || ev.event === "error") {
    void sessionsStore.loadAll();
    void projectsStore.load();
  }
}

const stream = useChatStream(
  () => agentsStore.activeAgentId,
  onStreamEvent,
);

const activeProjectId = computed(() => agentsStore.activeAgent?.projectId ?? "");
const profileOptions = computed(() =>
  profilesStore.profilesOfProject(activeProjectId.value).map((p) => ({ label: p.name, value: p.id })),
);
const currentProfile = computed(() => agentsStore.activeAgent?.activeProfileId ?? null);

watch(activeProjectId, (pid) => {
  if (pid) void profilesStore.loadProjectProfiles(pid);
});

async function boot(): Promise<void> {
  await configStore.load();
  await mcpStore.load();
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
  try {
    await projectsStore.load();
  } catch (e) {
    bootError.value = e instanceof Error ? e.message : String(e);
  }
  await profilesStore.load();
  if (agentsStore.activeAgent === null) {
    await agentsStore.createAgent(undefined, projectsStore.activeProjectId ?? undefined);
  }
}

onMounted(boot);

function cmdContext(): CommandContext {
  return {
    requests: {
      note: (text) => {
        const id = agentsStore.activeAgentId;
        const state = id !== null ? agentsStore.agents[id] : undefined;
        if (state !== undefined) {
          state.history.push({ id: `note-${Date.now()}`, role: "system", content: text });
        }
      },
    },
    actions: {
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
      exportSession: async (path) => {
        const id = agentsStore.activeAgentId;
        if (id === null) return "Нет активного агента.";
        const res = await api.exportSession(id, path);
        return `Экспортировано: ${res.path}`;
      },
    },
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
let pendingDeleteProject: string | null = null;
function confirmAsk(text: string, action: "close" | "delete-session" | "delete-project"): Promise<boolean> {
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
  } else if (ok && ov.kind === "confirm" && ov.action === "delete-project") {
    if (pendingDeleteProject !== null) void doDeleteProject(pendingDeleteProject);
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

function onProfileChange(profileId: string): void {
  void patchActive({ active_profile_id: profileId });
}

function onSessionSelect(sessionId: string): void {
  const id = agentsStore.activeAgentId;
  if (id !== null) void agentsStore.loadSession(id, sessionId);
}

async function onSessionBranch(sessionId: string): Promise<void> {
  await agentsStore.branchFromSession(sessionId);
  await sessionsStore.loadAll();
  await projectsStore.load();
}

async function onSessionRename(sessionId: string, title: string): Promise<void> {
  await sessionsStore.rename(sessionId, title);
}

async function onSessionDelete(sessionId: string): Promise<void> {
  const ok = await confirmAsk("Удалить сессию? Действие необратимо.", "delete-session");
  if (!ok) return;
  await sessionsStore.remove(sessionId);
}

async function onNewChat(projectId?: string): Promise<void> {
  await agentsStore.createAgent(undefined, projectId ?? projectsStore.activeProjectId ?? undefined);
}

async function onCreateProject(name: string): Promise<void> {
  await projectsStore.create(name);
}

async function onRenameProject(projectId: string, name: string): Promise<void> {
  await projectsStore.rename(projectId, name);
}

async function onDeleteProject(projectId: string): Promise<void> {
  pendingDeleteProject = projectId;
  const ok = await confirmAsk("Удалить проект и все его сессии? Действие необратимо.", "delete-project");
  if (ok) await doDeleteProject(projectId);
}

async function doDeleteProject(projectId: string): Promise<void> {
  await projectsStore.remove(projectId);
  await agentsStore.loadAll();
  await sessionsStore.loadAll();
}

function onSelectProject(_projectId: string): void {
  // активный проект уже установлен в сайдбаре; подхватываем его в хранилищах при новом чате
}

function onProjectMemory(projectId: string): void {
  const name = projectsStore.projects.find((p) => p.id === projectId)?.name ?? "Проект";
  overlay.value = { kind: "project-memory", projectId, projectName: name };
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
              @create-project="onCreateProject"
              @rename-project="onRenameProject"
              @delete-project="onDeleteProject"
              @select-project="onSelectProject"
              @memory="onProjectMemory"
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
                  :profile-options="profileOptions"
                  :current-profile="currentProfile"
                  :on-send="onSend"
                  :on-stop="onStop"
                  :on-model-change="onModelChange"
                  :on-profile-change="onProfileChange"
                />
              </template>
            </main>
          </n-layout-content>
        </n-layout>

        <!-- Palettes & Modals -->
        <n-modal
          v-if="overlay.kind === 'project-memory'"
          :show="true"
          preset="card"
          :title="`Память проекта — ${overlay.projectName}`"
          style="width: 560px;"
          @close="overlay = { kind: 'none' }"
        >
          <ProjectMemoryModal
            :project-id="overlay.projectId"
            :project-name="overlay.projectName"
            @close="overlay = { kind: 'none' }"
            @updated="projectsStore.load()"
          />
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