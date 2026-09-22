<script setup lang="ts">
// Панель задачи (TODO): конечный автомат задачи — создать, пауза/продолжить,
// подтвердить план, следующий шаг (с авто-переходом этапа), сбросить. Вынесена из
// MemoryPanel в отдельную вкладку верхней панели (рядом с памятью, профилями, настройками).
import { computed, ref } from "vue";
import { NButton, NInput, NTag } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";
import type { TaskPhase } from "@/api/types";

const store = useAgentsStore();
const agent = computed(() => store.activeAgent);

const PHASE_LABELS: Record<TaskPhase, string> = {
  idle: "без задачи",
  planning: "планирование",
  execution: "выполнение",
  validation: "проверка",
  done: "готово",
};

const task = computed(() => agent.value?.task ?? null);
const invariants = computed(() => agent.value?.invariants ?? []);
const taskError = ref<string | null>(null);
const startDescription = ref("");
const startSteps = ref("");
const startValidationSteps = ref("");
const taskBusy = ref(false);

/** Активный список шагов: в «проверке» — шаги проверки, иначе — шаги выполнения.
 * В «готово» шагов не показываем (это выдача финального результата). */
const activeList = computed(() => {
  const t = task.value;
  if (t === null || t.phase === "done") return [];
  return t.phase === "validation" ? t.validation_steps : t.steps;
});

function taskStepText(): string {
  const t = task.value;
  const list = activeList.value;
  if (t === null || list.length === 0) return "";
  const n = Math.max(0, Math.min(t.step, list.length));
  if (n <= 0) return "";
  return `Шаг ${n} из ${list.length}`;
}

/** Отметки для шага: this list выполнен/текущий с учётом активного списка. */
function stepState(i: number, kind: "exec" | "val"): { done: boolean; current: boolean } {
  const t = task.value;
  if (t === null) return { done: false, current: false };
  const idx = i + 1;
  if (kind === "exec") {
    // после выполнения — все шаги выполнения считаются выполненными
    if (t.phase === "validation" || t.phase === "done") return { done: true, current: false };
    return { done: idx < t.step, current: idx === t.step };
  }
  // шаги проверки начинаются только в фазе «проверка»/«готово»
  if (t.phase === "planning" || t.phase === "execution") return { done: false, current: false };
  // «готово» — вся проверка пройдена, все шаги проверки выполнены
  if (t.phase === "done") return { done: true, current: false };
  return { done: idx < t.step, current: idx === t.step };
}

function stepClass(i: number, kind: "exec" | "val"): Record<string, boolean> {
  const s = stepState(i, kind);
  return { "task-step-done": s.done, "task-step-current": s.current };
}

/** Подпись кнопки прохождения шага: на последнем шаге этапа — переход в следующий этап. */
const advanceLabel = computed(() => {
  const t = task.value;
  if (t === null) return "Шаг выполнен";
  if (t.phase === "execution") {
    return t.step >= t.steps.length ? "Завершить и проверить" : "Шаг выполнен (дальше)";
  }
  if (t.phase === "validation") {
    return t.step >= t.validation_steps.length ? "Завершить задачу" : "Шаг выполнен (дальше)";
  }
  return "Шаг выполнен";
});

async function runTaskCommand(fn: () => Promise<void>): Promise<boolean> {
  taskBusy.value = true;
  taskError.value = null;
  try {
    await fn();
    return true;
  } catch (e) {
    taskError.value = e instanceof Error ? e.message : String(e);
    return false;
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
async function onAdvance(): Promise<void> {
  const t = task.value;
  if (t === null || agent.value === null) return;
  const id = agent.value.id;
  await runTaskCommand(async () => {
    // последний шаг этапа — авто-переход в следующий этап (выполнение → проверка → готово)
    if (t.phase === "execution" && t.step >= t.steps.length) {
      await store.setTaskPhase(
        id,
        "validation",
        "все шаги выполнения выполнены — проверь результат и при необходимости внеси правки",
      );
    } else if (t.phase === "validation" && t.step >= t.validation_steps.length) {
      await store.setTaskPhase(
        id,
        "done",
        "проверка пройдена — верни отчёт о проделанной работе",
      );
    } else {
      await store.advanceTaskStep(id);
    }
  });
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
  const validationSteps = startValidationSteps.value
    .split("\n")
    .map((s) => s.trim())
    .filter((s) => s.length > 0);
  await runTaskCommand(() => store.startTask(agent.value!.id, description, steps, undefined, validationSteps));
}
const CONTINUATION_TEXT =
  "План подтверждён. Приступай к выполнению плана: выполни текущий шаг, при необходимости делегируя его субагенту через delegate.";

async function onConfirmPlan(): Promise<void> {
  if (agent.value === null) return;
  const ok = await runTaskCommand(() => store.confirmPlan(agent.value!.id));
  if (ok) await store.runStream(agent.value!.id, CONTINUATION_TEXT);
}
</script>

<template>
  <div class="task-panel">
    <template v-if="task === null">
      <p class="hint">
        Поставьте задачу агенту: опишите цель и шаги плана — агент будет вести
        конечный автомат (этапы, шаги, паузы) и видеть его в каждом запросе.
      </p>
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
          placeholder="Шаги выполнения — каждый с новой строки"
        />
        <n-input
          v-model:value="startValidationSteps"
          type="textarea"
          size="small"
          :autosize="{ minRows: 2, maxRows: 5 }"
          placeholder="Шаги проверки — каждый с новой строки (необязательно)"
        />
        <n-button size="small" type="primary" :disabled="!startDescription.trim()" :loading="taskBusy" @click="onStart">
          Начать
        </n-button>
      </div>
    </template>
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
      <template v-if="task.phase === 'planning' && invariants.length > 0">
        <p class="task-list-label">Ограничения (из запроса)</p>
        <ul class="task-steps">
          <li v-for="(inv, i) in invariants" :key="'i' + i">
            <span class="task-step-check checked" aria-hidden="true"></span>
            {{ inv }}
          </li>
        </ul>
      </template>
      <template v-if="task.phase !== 'done' && task.steps.length > 0">
        <p class="task-list-label">Шаги выполнения</p>
        <ul class="task-steps">
          <li
            v-for="(step, i) in task.steps"
            :key="'e' + i"
            :class="stepClass(i, 'exec')"
          >
            <span class="task-step-check" :class="{ checked: stepState(i, 'exec').done }" aria-hidden="true"></span>
            {{ step }}
          </li>
        </ul>
      </template>
      <template v-if="task.phase !== 'done' && task.validation_steps.length > 0">
        <p class="task-list-label">Шаги проверки</p>
        <ul class="task-steps">
          <li
            v-for="(step, i) in task.validation_steps"
            :key="'v' + i"
            :class="stepClass(i, 'val')"
          >
            <span class="task-step-check" :class="{ checked: stepState(i, 'val').done }" aria-hidden="true"></span>
            {{ step }}
          </li>
        </ul>
      </template>
      <div v-if="taskError" class="lt-error">{{ taskError }}</div>
      <div class="task-actions">
        <n-button v-if="task.paused" size="small" type="primary" :loading="taskBusy" @click="onResume">Продолжить</n-button>
        <n-button v-else size="small" :loading="taskBusy" @click="onPause">Пауза</n-button>
        <n-button
          v-if="task.phase === 'planning' && task.steps.length > 0 && !task.paused"
          size="small"
          type="primary"
          :loading="taskBusy"
          @click="onConfirmPlan"
        >
          Подтвердить план
        </n-button>
        <n-button
          v-if="task.phase === 'execution' || task.phase === 'validation'"
          size="small"
          type="primary"
          :loading="taskBusy"
          @click="onAdvance"
        >
          {{ advanceLabel }}
        </n-button>
        <n-button size="small" quaternary :loading="taskBusy" @click="onReset">Сбросить</n-button>
      </div>
    </template>
  </div>
</template>

<style scoped>
.task-panel {
  padding-top: 4px;
}
.hint {
  margin: 0 0 8px;
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  line-height: 1.5;
}
.task-start {
  display: flex;
  flex-direction: column;
  gap: 8px;
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
.task-list-label {
  margin: 8px 0 4px;
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
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
.lt-error {
  color: #e05c5c;
  font-size: 12px;
  margin: 6px 0;
}
</style>
