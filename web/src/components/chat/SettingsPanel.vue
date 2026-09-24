<script setup lang="ts">
// Панель «Настройки» активного агента: параметры генерации, системный промпт,
// стратегия контекста и facts-блок. Изменения сохраняются только в сессию агента
// (PATCH /api/agents/{id}); глобальный config.json не трогается.
import { computed, ref, watch } from "vue";
import { NButton, NCollapse, NCollapseItem, NInput, NRadioButton, NRadioGroup, NSlider, NTooltip } from "naive-ui";
import { api } from "@/api/client";
import { useAgentsStore } from "@/stores/agents";
import type { ContextStrategy } from "@/api/types";

const props = defineProps<{ active: boolean }>();

const store = useAgentsStore();
const agent = computed(() => store.activeAgent);

// --- параметры генерации (черновик, применяется по кнопке) ---
const temperature = ref("0.7");
const topP = ref("1.0");
const maxTokens = ref("4096");
const stopStr = ref("");
const contextStrategy = ref<ContextStrategy>("summary");
const slidingWindow = ref("20");
const compactionThreshold = ref(60); // в процентах (50–100)

const strategyOptions: { value: ContextStrategy; label: string; tip: string }[] = [
  { value: "none", label: "none", tip: "Вся история передаётся модели без сжатия." },
  { value: "summary", label: "summary", tip: "История автоматически сжимается в саммари при заполнении окна." },
  { value: "sliding", label: "sliding", tip: "Модели уходит только скользящее окно последних сообщений." },
  { value: "facts", label: "facts", tip: "Факт-блок (ключ-значение) + скользящее окно." },
];

watch(
  () => agent.value?.settings,
  (s) => {
    if (s === undefined) return;
    temperature.value = String(s.temperature);
    topP.value = String(s.top_p);
    maxTokens.value = String(s.max_tokens);
    stopStr.value = s.stop.join(", ");
    contextStrategy.value = s.context_strategy;
    slidingWindow.value = String(s.sliding_window);
    compactionThreshold.value = Math.round(s.compaction_threshold * 100);
  },
  { immediate: true },
);

const saving = ref(false);
const error = ref<string | null>(null);

function numInRange(v: string, min: number, max: number): number | null {
  const n = Number(v);
  if (Number.isNaN(n) || n < min || n > max) return null;
  return n;
}

async function saveSettings(): Promise<void> {
  const id = agent.value?.id;
  if (id === undefined) return;
  const t = numInRange(temperature.value, 0, 2);
  if (t === null) { error.value = "temperature — число от 0 до 2"; return; }
  const p = numInRange(topP.value, 0, 1);
  if (p === null) { error.value = "top-p — число от 0 до 1"; return; }
  const m = Math.trunc(Number(maxTokens.value));
  if (Number.isNaN(m) || m <= 0) { error.value = "max-tokens — целое число > 0"; return; }
  const w = Math.trunc(Number(slidingWindow.value));
  if (Number.isNaN(w) || w <= 0) { error.value = "sliding_window — целое число > 0"; return; }
  const ct = compactionThreshold.value / 100;
  if (ct < 0.5 || ct > 1.0) { error.value = "compaction_threshold — от 50% до 100%"; return; }
  const stop = stopStr.value.split(",").map((s) => s.trim()).filter((s) => s !== "");
  error.value = null;
  saving.value = true;
  try {
    await store.patchAgent(id, {
      temperature: t,
      top_p: p,
      max_tokens: m,
      stop,
      context_strategy: contextStrategy.value,
      sliding_window: w,
      compaction_threshold: ct,
    });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    saving.value = false;
  }
}

// --- системный промпт (путь + просмотр содержимого) ---
const promptPath = ref("");
const promptContent = ref("");

watch(
  () => [agent.value?.systemPromptPath, agent.value?.systemPromptContent] as const,
  ([path, content]) => {
    if (path === undefined) return;
    promptPath.value = path;
    promptContent.value = content ?? "";
  },
  { immediate: true },
);

const savingPrompt = ref(false);

async function savePromptPath(): Promise<void> {
  const path = promptPath.value.trim();
  if (path === "") { error.value = "путь к системному промпту не может быть пустым"; return; }
  error.value = null;
  savingPrompt.value = true;
  try {
    const res = await api.putSystemPrompt(path);
    promptPath.value = res.path;
    promptContent.value = res.content;
    const id = agent.value?.id;
    if (id !== undefined) await store.patchAgent(id, { system_prompt_path: path });
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    savingPrompt.value = false;
  }
}

// --- facts (стратегия facts): ключ-значение память диалога ---
interface FactRow {
  key: string;
  value: string;
}

const factRows = ref<FactRow[]>([]);
const factsLoaded = ref(false);
const savingFacts = ref(false);

async function loadFacts(): Promise<void> {
  const id = agent.value?.id;
  if (id === undefined) return;
  try {
    const facts = await api.getFacts(id);
    factRows.value = Object.entries(facts).map(([key, value]) => ({ key, value }));
    factsLoaded.value = true;
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  }
}

watch(
  () => props.active,
  (active) => {
    if (active) void loadFacts();
  },
  { immediate: true },
);

function addFact(): void {
  factRows.value.push({ key: "", value: "" });
}

function removeFact(index: number): void {
  factRows.value.splice(index, 1);
}

async function saveFacts(): Promise<void> {
  const id = agent.value?.id;
  if (id === undefined) return;
  const record: Record<string, string> = {};
  for (const row of factRows.value) {
    const key = row.key.trim();
    if (key === "") continue;
    record[key] = row.value;
  }
  error.value = null;
  savingFacts.value = true;
  try {
    await api.putFacts(id, record);
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e);
  } finally {
    savingFacts.value = false;
  }
}
</script>

<template>
  <div class="settings-panel">
    <n-collapse :default-expanded-names="['generation', 'prompt', 'context']">
      <n-collapse-item title="Параметры генерации" name="generation">
        <div class="form-row">
          <label class="form-label">temperature (0–2)</label>
          <n-input v-model:value="temperature" size="small" type="text" />
        </div>
        <div class="form-row">
          <label class="form-label">top-p (0–1)</label>
          <n-input v-model:value="topP" size="small" type="text" />
        </div>
        <div class="form-row">
          <label class="form-label">max-tokens</label>
          <n-input v-model:value="maxTokens" size="small" type="text" />
        </div>
        <div class="form-row">
          <label class="form-label">stop-последовательности (через запятую)</label>
          <n-input v-model:value="stopStr" size="small" type="text" placeholder="например, «Конец», END" />
        </div>
        <n-button size="small" type="primary" :loading="saving" @click="saveSettings">
          Сохранить
        </n-button>
      </n-collapse-item>
      <n-collapse-item title="Системный промпт" name="prompt">
        <p class="hint">Файл с системным промптом (путь и содержимое).</p>
        <div class="form-row">
          <label class="form-label">Путь</label>
          <n-input v-model:value="promptPath" size="small" type="text" />
        </div>
        <div class="form-row">
          <n-button size="small" type="primary" :loading="savingPrompt" @click="savePromptPath">
            Применить путь
          </n-button>
        </div>
        <pre class="prompt-view">{{ promptContent }}</pre>
      </n-collapse-item>
      <n-collapse-item title="Стратегия контекста" name="context">
        <p class="hint">Выберите стратегию — ниже появится её настройка.</p>
        <n-radio-group v-model:value="contextStrategy" size="small">
          <n-tooltip
            v-for="opt in strategyOptions"
            :key="opt.value"
            trigger="hover"
            :content="opt.tip"
          >
            <template #trigger>
              <n-radio-button :value="opt.value">{{ opt.label }}</n-radio-button>
            </template>
          </n-tooltip>
        </n-radio-group>

        <div v-if="contextStrategy === 'none'" class="hint strategy-note">
          Вся история передаётся модели без сжатия.
        </div>

        <div v-else-if="contextStrategy === 'summary'" class="form-row">
          <label class="form-label">Порог автосжатия: {{ compactionThreshold }}%</label>
          <n-slider v-model:value="compactionThreshold" :min="50" :max="100" :step="1" />
        </div>

        <div v-else-if="contextStrategy === 'sliding'" class="form-row">
          <label class="form-label">Скользящее окно (сообщений)</label>
          <n-input v-model:value="slidingWindow" size="small" type="text" />
        </div>

        <template v-else>
          <p class="hint">Ключ-значение память диалога; агент видит их в каждом запросе.</p>
          <div v-if="!factsLoaded" class="lt-empty">загрузка…</div>
          <template v-else>
            <div class="lt-list">
              <div v-for="(row, index) in factRows" :key="index" class="lt-item">
                <n-input v-model:value="row.key" size="small" placeholder="Ключ" />
                <n-input v-model:value="row.value" size="small" placeholder="Значение" />
                <div class="lt-actions">
                  <n-button size="tiny" quaternary @click="removeFact(index)">✕</n-button>
                </div>
              </div>
            </div>
            <div class="lt-add">
              <n-button size="tiny" quaternary @click="addFact">+ Добавить факт</n-button>
              <n-button size="small" type="primary" :loading="savingFacts" @click="saveFacts">
                Сохранить факты
              </n-button>
            </div>
          </template>
          <div class="form-row">
            <label class="form-label">Скользящее окно (сообщений)</label>
            <n-input v-model:value="slidingWindow" size="small" type="text" />
          </div>
        </template>

        <n-button size="small" type="primary" :loading="saving" @click="saveSettings">
          Сохранить
        </n-button>
      </n-collapse-item>
    </n-collapse>
    <div v-if="error" class="lt-error">{{ error }}</div>
  </div>
</template>

<style scoped>
.settings-panel {
  padding-top: 4px;
}
.hint {
  margin: 0 0 8px;
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  line-height: 1.5;
}
.strategy-note {
  margin: 8px 0;
}
.form-row {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin-bottom: 10px;
}
.form-label {
  font-size: 12px;
  color: var(--n-text-color-disabled, #888);
}
:deep(.n-radio-group) {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
  margin-bottom: 12px;
}
.prompt-view {
  margin: 8px 0 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: var(--mono);
  font-size: 12px;
  max-height: 260px;
  overflow: auto;
  color: #c8ccd4;
  background: #14161c;
  border: 1px solid var(--n-border-color, #3a3a3f);
  border-radius: 6px;
  padding: 8px;
}
.lt-list {
  display: flex;
  flex-direction: column;
  gap: 4px;
  margin: 8px 0;
}
.lt-item {
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 6px 8px;
  border: 1px solid var(--n-border-color, #3a3a3f);
  border-radius: 6px;
}
.lt-actions {
  display: flex;
  justify-content: flex-end;
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
