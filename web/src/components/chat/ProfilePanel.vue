<script setup lang="ts">
// Панель «Профили»: глобальный пул профилей роли + выбор, какие профили доступны
// текущему проекту. Активный профиль чата выбирается селектором рядом с моделью.
import { computed, ref, watch } from "vue";
import { NCollapse, NCollapseItem, NInput, NButton, NCheckbox } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";
import { useProfilesStore } from "@/stores/profiles";
import { useProjectsStore } from "@/stores/projects";

const props = defineProps<{ active: boolean }>();

const agentsStore = useAgentsStore();
const profilesStore = useProfilesStore();
const projectsStore = useProjectsStore();

const error = ref<string | null>(null);

const agent = computed(() => agentsStore.activeAgent);
const projectId = computed(() => agent.value?.projectId ?? "");
const projectName = computed(
  () => projectsStore.projects.find((p) => p.id === projectId.value)?.name ?? "Проект",
);

const profiles = computed(() => profilesStore.profiles);
const selectedIds = computed(() => {
  const list = profilesStore.projectProfiles[projectId.value] ?? [];
  return new Set(list.map((p) => p.id));
});

watch(
  () => props.active,
  (active) => {
    if (active) void reload();
  },
  { immediate: true },
);

watch(
  projectId,
  () => {
    if (props.active) void loadProject();
  },
);

async function reload(): Promise<void> {
  error.value = null;
  try {
    await profilesStore.load();
    await loadProject();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

async function loadProject(): Promise<void> {
  if (!projectId.value) return;
  try {
    await profilesStore.loadProjectProfiles(projectId.value);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

// --- создание / редактирование / удаление ---
const newName = ref("");
const newContent = ref("");
const creating = ref(false);

async function addProfile(): Promise<void> {
  const name = newName.value.trim();
  const content = newContent.value;
  if (!name) return;
  creating.value = true;
  error.value = null;
  try {
    await profilesStore.create(name, content);
    newName.value = "";
    newContent.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    creating.value = false;
  }
}

const editId = ref<string | null>(null);
const editName = ref("");
const editContent = ref("");

function startEdit(id: string): void {
  const p = profilesStore.profileById(id);
  if (p === undefined) return;
  editId.value = id;
  editName.value = p.name;
  editContent.value = p.content;
}

async function saveEdit(): Promise<void> {
  const id = editId.value;
  if (id === null) return;
  const name = editName.value.trim();
  if (!name) return;
  editId.value = null;
  error.value = null;
  try {
    await profilesStore.update(id, { name, content: editContent.value });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function cancelEdit(): void {
  editId.value = null;
}

async function removeProfile(id: string): Promise<void> {
  error.value = null;
  try {
    await profilesStore.remove(id);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

// --- привязка профилей к проекту ---
async function toggleBinding(profileId: string, checked: boolean): Promise<void> {
  const current = (profilesStore.projectProfiles[projectId.value] ?? []).map((p) => p.id);
  const next = checked ? [...current, profileId] : current.filter((id) => id !== profileId);
  error.value = null;
  try {
    await profilesStore.bind(projectId.value, next);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
    await loadProject();
  }
}

function isSelected(profileId: string): boolean {
  return selectedIds.value.has(profileId);
}
</script>

<template>
  <div class="profile-panel">
    <n-collapse :default-expanded-names="['project', 'global']">
      <n-collapse-item :title="`Профили проекта (${projectName})`" name="project">
        <p class="hint">
          Отметьте профили роли из глобального пула, доступные для этого проекта.
          Активный профиль выбирается в чате — селектором рядом с моделью.
        </p>
        <div v-if="error" class="p-error">{{ error }}</div>
        <div v-if="profiles.length === 0" class="p-empty">
          Глобальных профилей пока нет — создайте ниже.
        </div>
        <ul v-else class="p-list">
          <li v-for="p in profiles" :key="p.id" class="p-item">
            <n-checkbox
              :checked="isSelected(p.id)"
              :label="p.name"
              @update:checked="(v: boolean) => toggleBinding(p.id, v)"
            />
          </li>
        </ul>
      </n-collapse-item>
      <n-collapse-item title="Глобальные профили" name="global">
        <p class="hint">Профиль — системный промпт роли ассистента. Создавайте и редактируйте здесь.</p>
        <ul v-if="profiles.length > 0" class="g-list">
          <li v-for="p in profiles" :key="p.id" class="g-item">
            <template v-if="editId === p.id">
              <n-input v-model:value="editName" size="small" placeholder="Имя профиля" />
              <n-input
                v-model:value="editContent"
                type="textarea"
                size="small"
                placeholder="Текст роли…"
                :autosize="{ minRows: 2, maxRows: 6 }"
              />
              <div class="g-actions">
                <n-button size="tiny" type="primary" @click="saveEdit">Сохранить</n-button>
                <n-button size="tiny" quaternary @click="cancelEdit">Отмена</n-button>
              </div>
            </template>
            <template v-else>
              <div class="g-head">
                <span class="g-name">{{ p.name }}</span>
                <span class="g-actions">
                  <n-button size="tiny" quaternary @click="startEdit(p.id)">✏️</n-button>
                  <n-button size="tiny" quaternary @click="removeProfile(p.id)">✕</n-button>
                </span>
              </div>
              <span class="g-content" :title="p.content">{{ p.content }}</span>
            </template>
          </li>
        </ul>
        <div class="g-add">
          <n-input v-model:value="newName" size="small" placeholder="Имя профиля" />
          <n-input
            v-model:value="newContent"
            type="textarea"
            size="small"
            placeholder="Текст роли — например: «Ты — senior-разработчик…»"
            :autosize="{ minRows: 2, maxRows: 6 }"
          />
          <n-button size="small" :loading="creating" :disabled="!newName.trim()" @click="addProfile">
            Добавить профиль
          </n-button>
        </div>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<style scoped>
.profile-panel {
  padding-top: 4px;
}
.hint {
  margin: 0 0 8px;
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  line-height: 1.5;
}
.p-list,
.g-list {
  list-style: none;
  margin: 8px 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.p-item,
.g-item {
  padding: 6px 8px;
  border: 1px solid var(--n-border-color, #3a3a3f);
  border-radius: 6px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.g-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.g-name {
  font-size: 13px;
  font-weight: 600;
}
.g-content {
  font-size: 12px;
  line-height: 1.4;
  color: var(--n-text-color-disabled, #666);
  overflow-wrap: anywhere;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.g-actions {
  display: flex;
  gap: 2px;
  flex-shrink: 0;
}
.g-add {
  display: flex;
  flex-direction: column;
  gap: 8px;
  margin-top: 12px;
}
.p-error {
  color: #e05c5c;
  font-size: 12px;
  margin: 6px 0;
}
.p-empty {
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  padding: 8px 0;
}
</style>
