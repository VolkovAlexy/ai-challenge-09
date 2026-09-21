<script setup lang="ts">
import { onMounted, ref } from "vue";
import { NButton, NInput, NScrollbar } from "naive-ui";
import type { LongTermDTO } from "@/api/types";
import { api } from "@/api/client";

const props = defineProps<{
  projectId: string;
  projectName: string;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "updated"): void;
}>();

const dto = ref<LongTermDTO | null>(null);
const loading = ref(false);
const error = ref<string | null>(null);
const newText = ref("");
const adding = ref(false);
const editIndex = ref<number | null>(null);
const editDraft = ref("");

async function reload(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    dto.value = await api.getLongterm(props.projectId);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

onMounted(reload);

async function addEntry(): Promise<void> {
  const content = newText.value.trim();
  if (!content) return;
  adding.value = true;
  error.value = null;
  try {
    dto.value = await api.remember(content, props.projectId);
    newText.value = "";
    emit("updated");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    adding.value = false;
  }
}

function startEdit(index: number): void {
  editIndex.value = index;
  editDraft.value = dto.value?.entries[index] ?? "";
}

async function saveEdit(): Promise<void> {
  const index = editIndex.value;
  editIndex.value = null;
  if (index === null) return;
  const content = editDraft.value.trim();
  if (!content || content === dto.value?.entries[index]) return;
  error.value = null;
  try {
    dto.value = await api.updateLongterm(index, content, props.projectId);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

function cancelEdit(): void {
  editIndex.value = null;
}

async function removeEntry(index: number): Promise<void> {
  error.value = null;
  try {
    dto.value = await api.forget(index, props.projectId);
    emit("updated");
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>

<template>
  <div class="pm">
    <p class="pm-sub">Долговременная память проекта «{{ projectName }}» — агент видит её в каждом запросе.</p>
    <div v-if="error" class="pm-error">{{ error }}</div>
    <div v-if="loading" class="pm-empty">загрузка…</div>
    <template v-else>
      <div v-if="dto === null || dto.entries.length === 0" class="pm-empty">
        Записей пока нет — добавьте ниже или примите предложение агента.
      </div>
      <n-scrollbar v-else style="max-height: 40vh;">
        <ul class="pm-list">
          <li v-for="(entry, index) in (dto?.entries ?? [])" :key="index" class="pm-item">
            <template v-if="editIndex === index">
              <n-input
                v-model:value="editDraft"
                size="small"
                autofocus
                @keyup.enter="saveEdit"
                @blur="saveEdit"
              />
              <div class="pm-actions">
                <n-button size="tiny" type="primary" @mousedown.prevent @click="saveEdit">Сохранить</n-button>
                <n-button size="tiny" quaternary @mousedown.prevent @click="cancelEdit">Отмена</n-button>
              </div>
            </template>
            <template v-else>
              <span class="pm-text" :title="entry">{{ entry }}</span>
              <span class="pm-actions">
                <n-button size="tiny" quaternary @click="startEdit(index)">✏️</n-button>
                <n-button size="tiny" quaternary @click="removeEntry(index)">✕</n-button>
              </span>
            </template>
          </li>
        </ul>
      </n-scrollbar>
      <div class="pm-add">
        <n-input
          v-model:value="newText"
          size="small"
          placeholder="Новое знание"
          @keyup.enter="addEntry"
        />
        <n-button size="small" :loading="adding" :disabled="!newText.trim()" @click="addEntry">
          Добавить
        </n-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.pm {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.pm-sub {
  margin: 0;
  color: #888;
  font-size: 13px;
}
.pm-error {
  color: #f7768e;
  font-size: 13px;
}
.pm-empty {
  color: #555;
  font-size: 13px;
  padding: 8px 0;
}
.pm-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.pm-item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  background: #1c212c;
  border: 1px solid #2a2e3a;
  border-radius: 6px;
  padding: 8px 10px;
}
.pm-text {
  color: #c8ccd4;
  font-size: 13px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.pm-actions {
  display: flex;
  gap: 4px;
  flex-shrink: 0;
}
.pm-add {
  display: flex;
  gap: 8px;
}
</style>
