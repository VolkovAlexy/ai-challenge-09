<script setup lang="ts">
// Панель памяти: вертикальные сворачиваемые блоки рабочей памяти (scratchpad) и
// долговременной памяти (список записей: добавить / отредактировать / удалить).
// Содержимое — то, что раньше показывалось в SettingsDrawer.
import { computed, ref, watch } from "vue";
import { NCollapse, NCollapseItem, NInput, NButton, NSelect, NTag } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";
import type { TaskPhase } from "@/api/types";

const props = defineProps<{ active: boolean }>();

const store = useAgentsStore();
const agent = computed(() => store.activeAgent);

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

// --- состояние задачи (конечный автомат) ---
const PHASE_LABELS: Record<TaskPhase, string> = {
  idle: "без задачи",
  planning: "планирование",
  execution: "выполнение",
  validation: "проверка",
  done: "готово",
};
const PHASE_OPTIONS = (Object.entries(PHASE_LABELS) as [TaskPhase, string][]).map(([value, label]) => ({
  value,
  label,
}));

const task = computed(() => agent.value?.task ?? null);
const phaseChoice = ref<TaskPhase>("planning");
const taskError = ref<string | null>(null);
const startDescription = ref("");
const startSteps = ref("");
const taskBusy = ref(false);

watch(
  () => task.value?.phase,
  (value) => {
    if (value && value !== "idle") phaseChoice.value = value;
  },
  { immediate: true },
);

function taskStepText(): string {
  const t = task.value;
  if (t === null || t.steps.length === 0) return "";
  return `Шаг ${Math.min(t.step, t.steps.length)} из ${t.steps.length}`;
}

async function runTaskCommand(fn: () => Promise<void>): Promise<void> {
  taskBusy.value = true;
  taskError.value = null;
  try {
    await fn();
  } catch (e) {
    taskError.value = e instanceof Error ? e.message : String(e);
  } finally {
    taskBusy.value = false;
  }
}

async function onPause(): Promise<void> {
  if (agent.value === null) return;
  await runTaskCommand(() => store.pauseTask(agent.value!.id));
}
async function onResume(): Promise<void> {
  if (agent.value === null) return;
  await runTaskCommand(() => store.resumeTask(agent.value!.id));
}
async function onChangePhase(): Promise<void> {
  if (agent.value === null) return;
  await runTaskCommand(() => store.setTaskPhase(agent.value!.id, phaseChoice.value));
}
async function onAdvance(): Promise<void> {
  if (agent.value === null) return;
  await runTaskCommand(() => store.advanceTaskStep(agent.value!.id));
}
async function onReset(): Promise<void> {
  if (agent.value === null) return;
  await runTaskCommand(() => store.resetTask(agent.value!.id));
}
async function onStart(): Promise<void> {
  if (agent.value === null) return;
  const description = startDescription.value.trim();
  if (!description) return;
  const steps = startSteps.value
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  await runTaskCommand(() => store.startTask(agent.value!.id, description, steps));
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
    <n-collapse :default-expanded-names="['task', 'scratchpad', 'longterm']">
      <n-collapse-item title="TODO" name="task">
        <div v-if="task === null" class="task-empty">
          <div class="task-start">
            <n-input
              v-model:value="startDescription"
              size="small"
              placeholder="Описание задачи"
              @keyup.enter="onStart"
            />
            <n-input
              v-model:value="startSteps"
              type="textarea"
              size="small"
              :autosize="{ minRows: 2, maxRows: 5 }"
              placeholder="Шаги плана — каждый с новой строки"
            />
            <n-button size="small" type="primary" :disabled="!startDescription.trim()" :loading="taskBusy" @click="onStart">
              Начать
            </n-button>
          </div>
        </div>
        <template v-else>
          <div class="task-status">
            <n-tag :bordered="false" :type="task.paused ? 'warning' : 'default'" size="small">
              {{ PHASE_LABELS[task.phase] }}
            </n-tag>
            <span v-if="task.paused" class="task-paused">на паузе</span>
          </div>
          <p v-if="taskStepText()" class="task-meta">{{ taskStepText() }}</p>
          <p v-if="task.description" class="task-desc">{{ task.description }}</p>
          <p v-if="task.expected_action" class="task-action">Ожидаемое действие: <strong>{{ task.expected_action }}</strong></p>
          <ul v-if="task.steps.length > 0" class="task-steps">
            <li
              v-for="(step, i) in task.steps"
              :key="i"
              :class="{
                'task-step-done': i + 1 < task.step,
                'task-step-current': i + 1 === task.step,
              }"
            >
              <span class="task-step-check" :class="{ checked: i + 1 < task.step }" aria-hidden="true"></span>
              {{ step }}
            </li>
          </ul>
          <div v-if="taskError" class="lt-error">{{ taskError }}</div>
          <div class="task-actions">
            <n-button v-if="task.paused" size="small" type="primary" :loading="taskBusy" @click="onResume">Продолжить</n-button>
            <n-button v-else size="small" :loading="taskBusy" @click="onPause">Пауза</n-button>
            <n-select
              v-model:value="phaseChoice"
              size="small"
              class="task-phase-select"
              :options="PHASE_OPTIONS"
              :disabled="taskBusy"
            />
            <n-button size="small" :loading="taskBusy" @click="onChangePhase">Сменить этап</n-button>
            <n-button size="small" quaternary :loading="taskBusy" @click="onAdvance">Следующий шаг</n-button>
            <n-button size="small" quaternary :loading="taskBusy" @click="onReset">Сбросить</n-button>
          </div>
        </template>
      </n-collapse-item>
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
.task-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.task-paused {
  color: #e8a24b;
  font-size: 12px;
}
.task-meta {
  margin: 0 0 6px;
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
}
.task-desc {
  margin: 0 0 6px;
  font-size: 13px;
}
.task-action {
  margin: 0 0 8px;
  font-size: 13px;
}
.task-steps {
  list-style: none;
  margin: 0 0 10px;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 3px;
}
.task-steps > li {
  display: flex;
  align-items: baseline;
  gap: 6px;
}
.task-step-check {
  flex: none;
  align-self: center;
  width: 14px;
  height: 14px;
  border: 1px solid var(--n-border-color, #444);
  border-radius: 3px;
  position: relative;
}
.task-step-check.checked {
  background: var(--n-color, #7aa2f7);
  border-color: var(--n-color, #7aa2f7);
}
.task-step-check.checked::after {
  content: "";
  position: absolute;
  left: 4px;
  top: 1px;
  width: 4px;
  height: 8px;
  border: solid #000;
  border-width: 0 2px 2px 0;
  transform: rotate(45deg);
}
.task-step-done {
  color: var(--n-text-color-disabled, #666);
}
.task-step-current {
  color: var(--n-text-color-1, #eee);
  font-weight: 600;
}
.task-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  align-items: center;
}
.task-phase-select {
  width: 160px;
}
.task-empty {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.task-start {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.lt-empty {
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  padding: 8px 0;
}
</style>
