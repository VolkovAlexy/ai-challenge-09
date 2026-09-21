<script setup lang="ts">
import { computed, onMounted, ref } from "vue";
import { NButton, NScrollbar, NDivider, NDropdown, NInput } from "naive-ui";
import type { ProjectDTO, SessionInfoDTO } from "@/api/types";
import { useProjectsStore } from "@/stores/projects";
import { useSessionsStore } from "@/stores/sessions";

defineProps<{
  activeSessionId: string | null;
}>();

const emit = defineEmits<{
  (e: "select", sessionId: string): void;
  (e: "newChat", projectId?: string): void;
  (e: "branch", sessionId: string): void;
  (e: "delete", sessionId: string): void;
  (e: "rename", sessionId: string, title: string): void;
  (e: "createProject", name: string): void;
  (e: "renameProject", projectId: string, name: string): void;
  (e: "deleteProject", projectId: string): void;
  (e: "selectProject", projectId: string): void;
  (e: "memory", projectId: string): void;
}>();

const projectsStore = useProjectsStore();
const sessionsStore = useSessionsStore();
const loaded = ref(false);

onMounted(async () => {
  await Promise.all([projectsStore.load(), sessionsStore.loadAll()]);
  loaded.value = true;
});

/** Сессии, сгруппированные по project_id. */
const byProject = computed<Record<string, SessionInfoDTO[]>>(() => {
  const groups: Record<string, SessionInfoDTO[]> = {};
  for (const s of sessionsStore.sessions) {
    const pid = s.project_id ?? "";
    (groups[pid] ??= []).push(s);
  }
  return groups;
});

const projectOptions = (project: ProjectDTO) => [
  { label: "Новый чат", key: "new-chat" },
  { label: "Память", key: "memory" },
  { label: "Переименовать", key: "rename" },
  { label: "Удалить", key: "delete", disabled: project.id === "default" },
];

const sessionCardOptions = [
  { label: "Бранч", key: "branch" },
  { label: "Изменить описание", key: "rename" },
  { label: "Удалить", key: "delete" },
];

const creating = ref(false);
const createDraft = ref("");
const renameProjectId = ref<string | null>(null);
const renameDraft = ref("");
const editingSessionId = ref<string | null>(null);
const editDraft = ref("");

function onProjectAction(key: string, project: ProjectDTO): void {
  if (key === "new-chat") emit("newChat", project.id);
  else if (key === "memory") emit("memory", project.id);
  else if (key === "rename") startProjectRename(project);
  else if (key === "delete") emit("deleteProject", project.id);
}

function startProjectRename(project: ProjectDTO): void {
  renameProjectId.value = project.id;
  renameDraft.value = project.name;
}

function cancelProjectRename(): void {
  renameProjectId.value = null;
}

function confirmProjectRename(project: ProjectDTO): void {
  if (renameProjectId.value !== project.id) return;
  const name = renameDraft.value.trim();
  renameProjectId.value = null;
  if (name !== "" && name !== project.name) emit("renameProject", project.id, name);
}

function startCreate(): void {
  creating.value = true;
  createDraft.value = "";
}

function cancelCreate(): void {
  creating.value = false;
  createDraft.value = "";
}

function confirmCreate(): void {
  if (!creating.value) return;
  const name = createDraft.value.trim();
  creating.value = false;
  if (name === "") return;
  emit("createProject", name);
  createDraft.value = "";
}

function onCardAction(key: string, session: SessionInfoDTO): void {
  if (key === "branch") emit("branch", session.id);
  else if (key === "rename") startRename(session);
  else if (key === "delete") emit("delete", session.id);
}

function startRename(session: SessionInfoDTO): void {
  editingSessionId.value = session.id;
  editDraft.value = session.title;
}

function cancelRename(): void {
  editingSessionId.value = null;
}

function confirmRename(session: SessionInfoDTO): void {
  if (editingSessionId.value !== session.id) return;
  const title = editDraft.value.trim();
  editingSessionId.value = null;
  if (title !== session.title) emit("rename", session.id, title);
}

function onProjectClick(project: ProjectDTO): void {
  projectsStore.setActive(project.id);
  projectsStore.toggle(project.id);
  emit("selectProject", project.id);
}

function formatDate(iso: string): string {
  const d = new Date(iso);
  const day = String(d.getDate()).padStart(2, "0");
  const month = String(d.getMonth() + 1).padStart(2, "0");
  const hours = String(d.getHours()).padStart(2, "0");
  const mins = String(d.getMinutes()).padStart(2, "0");
  return `${day}.${month} ${hours}:${mins}`;
}

function truncateModel(model: string | undefined): string {
  if (!model) return "";
  const parts = model.split(":");
  return parts.length > 1 ? parts[1] : parts[0];
}
</script>

<template>
  <div class="sidebar-inner">
    <div class="sidebar-header">
      <span>Проекты</span>
      <n-button size="tiny" quaternary @click="startCreate">+ Проект</n-button>
    </div>
    <n-divider style="margin: 0;" />

    <div v-if="projectsStore.loadError" class="sidebar-empty">
      {{ projectsStore.loadError }}
    </div>

    <div v-if="creating" class="create-wrap">
      <n-input
        v-model:value="createDraft"
        size="small"
        placeholder="Имя проекта"
        autofocus
        @keyup.enter="confirmCreate"
        @keyup.esc="cancelCreate"
        @blur="confirmCreate"
      />
    </div>

    <div v-else-if="projectsStore.projects.length === 0 && loaded" class="sidebar-empty">
      Нет проектов
    </div>

    <n-scrollbar v-else-if="projectsStore.projects.length > 0" style="flex: 1; min-height: 0;">
      <div class="sidebar-list">
        <div
          v-for="project in projectsStore.projects"
          :key="project.id"
          class="project-card"
          :class="{ active: project.id === projectsStore.activeProjectId }"
        >
          <!-- заголовок проекта -->
          <div class="project-head" @click="onProjectClick(project)">
            <span class="project-caret" :class="{ open: projectsStore.isExpanded(project.id) }">▸</span>
            <template v-if="renameProjectId === project.id">
              <input
                v-model="renameDraft"
                class="project-rename"
                autofocus
                @keyup.enter="confirmProjectRename(project)"
                @keyup.esc="cancelProjectRename"
                @blur="confirmProjectRename(project)"
              />
            </template>
            <template v-else>
              <span class="project-name">{{ project.name }}</span>
              <span class="project-meta">{{ (byProject[project.id] ?? []).length }} чатов</span>
            </template>
            <n-dropdown
              :options="projectOptions(project)"
              trigger="click"
              placement="right-start"
              @select="(key: string) => onProjectAction(key, project)"
            >
              <n-button class="project-menu" size="tiny" quaternary @click.stop>⋮</n-button>
            </n-dropdown>
          </div>

          <!-- тело проекта: сессии -->
          <div v-if="projectsStore.isExpanded(project.id)" class="project-body">
            <div v-if="(byProject[project.id] ?? []).length === 0" class="sidebar-empty-small">
              Нет чатов
            </div>
            <div
              v-for="session in (byProject[project.id] ?? [])"
              :key="session.id"
              class="session-card"
              :class="{ active: session.id === activeSessionId }"
              @click="emit('select', session.id)"
            >
              <template v-if="editingSessionId === session.id">
                <input
                  v-model="editDraft"
                  class="session-card-edit"
                  autofocus
                  @keyup.enter="confirmRename(session)"
                  @keyup.esc="cancelRename"
                  @blur="confirmRename(session)"
                />
              </template>
              <template v-else>
                <div class="session-card-title">{{ session.title }}</div>
                <div class="session-card-meta">
                  <span>{{ formatDate(session.updated_at) }}</span>
                  <span v-if="session.model" class="session-card-model">{{ truncateModel(session.model) }}</span>
                  <span v-if="session.message_count !== undefined">{{ session.message_count }}</span>
                </div>
              </template>
              <n-dropdown
                :options="sessionCardOptions"
                trigger="click"
                placement="right-start"
                @select="(key: string) => onCardAction(key, session)"
              >
                <n-button class="card-menu" size="tiny" quaternary @click.stop>⋮</n-button>
              </n-dropdown>
            </div>
            <n-button class="project-new-chat" size="tiny" quaternary @click="emit('newChat', project.id)">
              + Новый чат
            </n-button>
          </div>
        </div>
      </div>
    </n-scrollbar>
  </div>
</template>

<style scoped>
.sidebar-inner {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.sidebar-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px 8px;
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #777;
}

.sidebar-list {
  padding: 4px 8px 8px;
}

.sidebar-empty {
  padding: 24px 16px;
  color: #555;
  font-style: italic;
  font-size: 13px;
}

.sidebar-empty-small {
  padding: 8px 12px;
  color: #555;
  font-style: italic;
  font-size: 12px;
}

.create-wrap {
  padding: 8px 10px;
}

/* --- карточка проекта --- */
.project-card {
  margin: 2px 0;
  border: 1px solid #242833;
  border-radius: 8px;
  background: #1a1d25;
  overflow: hidden;
}

.project-card.active {
  border-color: #7aa2f7;
}

.project-head {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 10px;
  cursor: pointer;
  transition: background 0.15s;
}

.project-head:hover {
  background: #1c212c;
}

.project-caret {
  color: #777;
  font-size: 11px;
  transition: transform 0.15s;
  flex-shrink: 0;
}

.project-caret.open {
  transform: rotate(90deg);
}

.project-name {
  font-size: 13px;
  color: #c8ccd4;
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.project-meta {
  font-size: 11px;
  color: #555;
  flex-shrink: 0;
}

.project-rename {
  flex: 1;
  min-width: 0;
  background: #1c212c;
  border: 1px solid #7aa2f7;
  border-radius: 6px;
  color: #c8ccd4;
  font-size: 13px;
  padding: 4px 6px;
  outline: none;
}

.project-menu {
  flex-shrink: 0;
  color: #888;
  opacity: 0;
  transition: opacity 0.15s;
}

.project-head:hover .project-menu,
.project-card.active .project-menu {
  opacity: 1;
}

/* --- тело проекта: сессии --- */
.project-body {
  padding: 4px 6px 8px;
  border-top: 1px solid #242833;
}

.session-card {
  position: relative;
  padding: 8px 10px;
  margin: 2px 0;
  border-radius: 6px;
  cursor: pointer;
  border: 1px solid transparent;
  transition: background 0.15s, border-color 0.15s;
}

.session-card:hover {
  background: #1c212c;
  border-color: #2a2e3a;
}

.session-card.active {
  background: #1e2a3d;
  border-color: #7aa2f7;
}

.session-card-title {
  font-size: 12px;
  line-height: 1.4;
  color: #c8ccd4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
  word-break: break-word;
  padding-right: 22px;
}

.session-card-meta {
  display: flex;
  gap: 8px;
  font-size: 11px;
  color: #555;
  margin-top: 4px;
}

.session-card-model {
  color: #7aa2f7;
}

.session-card-edit {
  width: 100%;
  background: #1c212c;
  border: 1px solid #7aa2f7;
  border-radius: 6px;
  color: #c8ccd4;
  font-size: 13px;
  padding: 6px 8px;
  outline: none;
}

.card-menu {
  position: absolute;
  top: 6px;
  right: 6px;
  opacity: 0;
  transition: opacity 0.15s;
  color: #888;
}

.session-card:hover .card-menu,
.session-card.active .card-menu {
  opacity: 1;
}

.project-new-chat {
  margin: 6px 0 0 6px;
  width: calc(100% - 12px);
  justify-content: flex-start;
}
</style>
