<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NButton, NScrollbar, NDivider, NDropdown } from "naive-ui";
import type { SessionInfoDTO } from "@/api/types";
import { useSessionsStore } from "@/stores/sessions";

defineProps<{
  activeSessionId: string | null;
}>();

const emit = defineEmits<{
  (e: "select", sessionId: string): void;
  (e: "newChat"): void;
  (e: "branch", sessionId: string): void;
  (e: "delete", sessionId: string): void;
  (e: "rename", sessionId: string, title: string): void;
}>();

const sessionsStore = useSessionsStore();
const loaded = ref(false);

onMounted(async () => {
  await sessionsStore.load(20, 0);
  loaded.value = true;
});

const cardOptions = [
  { label: "Бранч", key: "branch" },
  { label: "Изменить описание", key: "rename" },
  { label: "Удалить", key: "delete" },
];

const editingId = ref<string | null>(null);
const editDraft = ref("");

function onCardAction(key: string, session: SessionInfoDTO): void {
  if (key === "branch") emit("branch", session.id);
  else if (key === "rename") startRename(session);
  else if (key === "delete") emit("delete", session.id);
}

function startRename(session: SessionInfoDTO): void {
  editingId.value = session.id;
  editDraft.value = session.title;
}

function cancelRename(): void {
  editingId.value = null;
}

function confirmRename(session: SessionInfoDTO): void {
  if (editingId.value !== session.id) return;
  const title = editDraft.value.trim();
  editingId.value = null;
  if (title !== session.title) emit("rename", session.id, title);
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
      <span>Чаты</span>
      <n-button size="tiny" quaternary @click="emit('newChat')">+ Новый</n-button>
    </div>
    <n-divider style="margin: 0;" />
    <div v-if="sessionsStore.loadError" class="sidebar-empty">
      {{ sessionsStore.loadError }}
    </div>
    <div v-else-if="sessionsStore.sessions.length === 0 && loaded" class="sidebar-empty">
      Нет сохранённых чатов
    </div>
    <n-scrollbar v-else style="flex: 1; min-height: 0;">
      <div class="sidebar-list">
        <div
          v-for="session in sessionsStore.sessions"
          :key="session.id"
          class="session-card"
          :class="{ active: session.id === activeSessionId }"
          @click="emit('select', session.id)"
        >
          <template v-if="editingId === session.id">
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
            :options="cardOptions"
            trigger="click"
            placement="right-start"
            @select="(key: string) => onCardAction(key, session)"
          >
            <n-button class="card-menu" size="tiny" quaternary @click.stop>⋮</n-button>
          </n-dropdown>
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

.session-card {
  position: relative;
  padding: 10px 12px;
  margin: 2px 0;
  border-radius: 8px;
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
  font-size: 13px;
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
  top: 8px;
  right: 8px;
  opacity: 0;
  transition: opacity 0.15s;
  color: #888;
}

.session-card:hover .card-menu,
.session-card.active .card-menu {
  opacity: 1;
}
</style>
