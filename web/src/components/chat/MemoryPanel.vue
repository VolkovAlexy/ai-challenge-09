<script setup lang="ts">
// Панель памяти: вертикальные сворачиваемые блоки рабочей памяти (scratchpad) и
// долговременной памяти (список записей: добавить / отредактировать / удалить).
// Состояние задачи (TODO) вынесено в отдельную вкладку TaskPanel верхней панели.
import { computed, ref, watch } from "vue";
import { NCollapse, NCollapseItem, NInput, NButton } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";

const props = defineProps<{ active: boolean }>();

const store = useAgentsStore();
const agent = computed(() => store.activeAgent);
const invariants = computed(() => agent.value?.invariants ?? []);

// --- рабочая память ---
const draft = ref("");
const editing = ref(false);

watch(
  () => agent.value?.scratchpad,
  (value) => {
    if (!editing.value) draft.value = value ?? "";
  },
  { immediate: true },
);

function onPadInput(value: string): void {
  draft.value = value;
  editing.value = true;
}

async function onPadBlur(): Promise<void> {
  editing.value = false;
  if (agent.value === null) return;
  if (draft.value !== agent.value.scratchpad) {
    await store.setScratchpad(agent.value.id, draft.value);
  }
}

// --- долговременная память ---
// Записи живут в сторе (общие для всех агентов), поэтому панель актуальна,
// даже когда память меняется извне (принять предложение, запомнить сообщение).
const entries = computed(() => store.longtermEntries);
const loading = ref(false);
const error = ref<string | null>(null);
const newText = ref("");
const adding = ref(false);
const editIndex = ref<number | null>(null);
const editDraft = ref("");

watch(
  () => props.active,
  (active) => {
    if (active) void reload();
  },
  { immediate: true },
);

async function reload(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    await store.refreshLongterm();
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    loading.value = false;
  }
}

async function addEntry(): Promise<void> {
  const content = newText.value.trim();
  if (!content) return;
  adding.value = true;
  error.value = null;
  try {
    await store.addLongterm(content);
    newText.value = "";
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    adding.value = false;
  }
}

function startEdit(index: number): void {
  editIndex.value = index;
  editDraft.value = entries.value[index] ?? "";
}

async function saveEdit(): Promise<void> {
  const index = editIndex.value;
  editIndex.value = null;
  if (index === null) return;
  const content = editDraft.value.trim();
  if (!content || content === entries.value[index]) return;
  error.value = null;
  try {
    await store.updateLongterm(index, content);
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
    await store.removeLongterm(index);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}
</script>

<template>
  <div class="memory-panel">
    <n-collapse :default-expanded-names="['scratchpad', 'invariants', 'longterm']">
      <n-collapse-item title="Рабочая память" name="scratchpad">
        <p class="hint">Заметки по текущей задаче — агент пишет сюда через инструмент write_scratchpad и видит содержимое в каждом запросе.</p>
        <n-input
          type="textarea"
          :value="draft"
          placeholder="Пусто — агент может писать сюда через инструмент write_scratchpad"
          :autosize="{ minRows: 4, maxRows: 16 }"
          size="small"
          @update:value="onPadInput"
          @blur="onPadBlur"
        />
      </n-collapse-item>
      <n-collapse-item title="Ограничения" name="invariants">
        <p class="hint">
          Обязательные требования к результату (инварианты). Агент добавляет и убирает их
          через инструменты invariant_add / invariant_remove и сверяется с ними на шаге ПРОВЕРКА.
          Управляются агентскими инструментами, а не редактированием вручную.
        </p>
        <div v-if="invariants.length === 0" class="lt-empty">Ограничений пока нет — агент записывает их через инструмент invariant_add.</div>
        <ul v-else class="lt-list">
          <li v-for="(item, index) in invariants" :key="index" class="lt-item">
            <span class="lt-text" :title="item">{{ item }}</span>
          </li>
        </ul>
      </n-collapse-item>
      <n-collapse-item title="Долговременная память" name="longterm">
        <p class="lt-hint">
          Знания общие для всех сессий, агент видит их в каждом запросе.
          Устаревшую запись отредактируйте или удалите.
        </p>
        <div v-if="error" class="lt-error">{{ error }}</div>
        <div v-if="loading" class="lt-empty">загрузка…</div>
        <div v-else-if="entries.length === 0" class="lt-empty">Записей пока нет — добавьте ниже или примите предложение агента.</div>
        <ul v-else class="lt-list">
          <li v-for="(entry, index) in entries" :key="index" class="lt-item">
            <template v-if="editIndex === index">
              <n-input
                v-model:value="editDraft"
                size="small"
                autofocus
                @keyup.enter="saveEdit"
                @blur="saveEdit"
              />
              <div class="lt-actions">
                <n-button size="tiny" type="primary" @mousedown.prevent @click="saveEdit">Сохранить</n-button>
                <n-button size="tiny" quaternary @mousedown.prevent @click="cancelEdit">Отмена</n-button>
              </div>
            </template>
            <template v-else>
              <span class="lt-text" :title="entry">{{ entry }}</span>
              <span class="lt-actions">
                <n-button size="tiny" quaternary @click="startEdit(index)">✏️</n-button>
                <n-button size="tiny" quaternary @click="removeEntry(index)">✕</n-button>
              </span>
            </template>
          </li>
        </ul>
        <div class="lt-add">
          <n-input
            v-model:value="newText"
            size="small"
            placeholder="Новое знание — например, обращайтесь ко мне «Босс»"
            @keyup.enter="addEntry"
          />
          <n-button size="small" :loading="adding" :disabled="!newText.trim()" @click="addEntry">
            Добавить
          </n-button>
        </div>
      </n-collapse-item>
    </n-collapse>
  </div>
</template>

<style scoped>
.memory-panel {
  padding-top: 4px;
}
.hint {
  margin: 0 0 8px;
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  line-height: 1.5;
}
.lt-list {
  list-style: none;
  margin: 8px 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.lt-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 8px;
  border: 1px solid var(--n-border-color, #3a3a3f);
  border-radius: 6px;
}
.lt-item > :first-child {
  align-self: stretch;
}
.lt-text {
  font-size: 13px;
  line-height: 1.4;
  overflow-wrap: anywhere;
}
.lt-actions {
  display: flex;
  gap: 2px;
}
.lt-add {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-top: 12px;
}
.lt-error {
  color: #e05c5c;
  font-size: 12px;
  margin: 6px 0;
}
.lt-empty {
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  padding: 8px 0;
}
</style>
