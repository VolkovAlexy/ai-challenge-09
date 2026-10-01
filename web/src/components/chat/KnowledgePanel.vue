<script setup lang="ts">
// Панель «Знания» (RAG): вкл/выкл, стратегия нарезки чанков, размер/перекрытие,
// принудительная переиндексация. Статус читается поллингом (2 c) — индекс
// строится в фоне, в этот момент показывается прогресс «индексация… X/Y».
// Настройки эфемерны: в config.json не пишутся, при рестарте сбрасываются.

import { NButton, NInputNumber, NSelect, NSwitch } from 'naive-ui';
import { computed, onMounted, onUnmounted, ref, watch } from 'vue';
import { useKnowledgeStore } from '@/stores/knowledge';

defineProps<{ active: boolean }>();

const store = useKnowledgeStore();
const knowledge = computed(() => store.knowledge);
const error = computed(() => store.loadError);

let timer: ReturnType<typeof setInterval> | null = null;
let mounted = false;

function stopPolling(): void {
  if (timer !== null) {
    clearInterval(timer);
    timer = null;
  }
}

async function poll(): Promise<void> {
  await store.load();
  if (!mounted) return;
  if (!store.knowledge?.indexing) stopPolling();
}

function startPolling(): void {
  if (timer !== null) return;
  timer = setInterval(() => void poll(), 2000);
  void poll();
}

onMounted(() => {
  mounted = true;
  void store.load();
});

onUnmounted(() => {
  mounted = false;
  stopPolling();
});

watch(
  () => knowledge.value?.indexing,
  (indexing) => {
    if (indexing) startPolling();
    else stopPolling();
  },
);

const enabled = ref(true);
const strategy = ref<'paragraph' | 'fixed'>('paragraph');
const chunkSize = ref(512);
const chunkOverlap = ref(64);
const topK = ref(4);
const retrieveTopK = ref(20);
const relevanceEnabled = ref(false);
const relevanceThreshold = ref(0.6);

watch(
  knowledge,
  (k) => {
    if (!k) return;
    enabled.value = k.enabled;
    strategy.value = k.chunk_strategy;
    chunkSize.value = k.chunk_size;
    chunkOverlap.value = k.chunk_overlap;
    topK.value = k.top_k;
    retrieveTopK.value = k.retrieve_top_k;
    relevanceEnabled.value = k.relevance_enabled;
    relevanceThreshold.value = k.relevance_threshold;
  },
  { immediate: true },
);

function toggleEnabled(v: boolean): void {
  void store.update({ enabled: v });
}

async function onStrategy(v: string): Promise<void> {
  await store.update({ chunk_strategy: v as 'paragraph' | 'fixed' });
  startPolling();
}

async function onChunkSize(v: number | null): Promise<void> {
  if (v === null) return;
  await store.update({ chunk_size: v });
  startPolling();
}

async function onChunkOverlap(v: number | null): Promise<void> {
  if (v === null) return;
  await store.update({ chunk_overlap: v });
  startPolling();
}

async function onTopK(v: number | null): Promise<void> {
  if (v === null) return;
  await store.update({ top_k: v });
}

async function onRetrieveTopK(v: number | null): Promise<void> {
  if (v === null) return;
  await store.update({ retrieve_top_k: v });
}

function onRelevanceEnabled(v: boolean): void {
  void store.update({ relevance_enabled: v });
}

async function onRelevanceThreshold(v: number | null): Promise<void> {
  if (v === null) return;
  await store.update({ relevance_threshold: v });
}

async function rebuild(): Promise<void> {
  await store.update({ rebuild: true });
  startPolling();
}

const STRATEGY_OPTIONS = [
  { label: 'по абзацам', value: 'paragraph' },
  { label: 'фиксированные куски', value: 'fixed' },
];

const status = computed(() => {
  const k = knowledge.value;
  if (!k) return { text: 'загрузка…', color: '#888' };
  if (!k.embedding_model) return { text: 'RAG не настроен', color: '#e5c07b' };
  if (k.error) return { text: 'ошибка индексации', color: '#e06c75' };
  if (k.indexing) return { text: `индексация… ${k.indexed}/${k.total}`, color: '#e5c07b' };
  if (!k.enabled) return { text: 'RAG выключен', color: '#888' };
  if (k.ready) return { text: `готово, ${k.size} чанков`, color: '#98c379' };
  return { text: 'индекс не готов', color: '#e5c07b' };
});
</script>

<template>
  <div class="knowledge-panel">
    <p class="hint">
      Файлы знаний для моделек (папка <code>knowledge</code>). Релевантные куски
      инжектятся в контекст при каждом ходе. Настройки ниже — эфемерные, при
      рестарте бэкенда возвращаются к config.json.
    </p>

    <div v-if="error" class="panel-error">{{ error }}</div>
    <div v-else-if="knowledge === null" class="panel-empty">загрузка…</div>

    <template v-else>
      <div class="row">
        <span class="label">Семантический поиск (RAG)</span>
        <n-switch size="small" :value="enabled" @update:value="toggleEnabled" />
      </div>

      <div class="row">
        <span class="label">Нарезка чанков</span>
        <n-select
          :value="strategy"
          :options="STRATEGY_OPTIONS"
          size="small"
          class="ctrl"
          @update:value="onStrategy"
        />
      </div>

      <div v-if="strategy === 'fixed'" class="row">
        <span class="label">Размер куска</span>
        <n-input-number
          :value="chunkSize"
          :min="1"
          :step="32"
          size="small"
          class="ctrl"
          @update:value="onChunkSize"
        />
      </div>

      <div v-if="strategy === 'fixed'" class="row">
        <span class="label">Перекрытие</span>
        <n-input-number
          :value="chunkOverlap"
          :min="0"
          :step="16"
          size="small"
          class="ctrl"
          @update:value="onChunkOverlap"
        />
      </div>

      <div class="row">
        <span class="label">RAG top-k</span>
        <n-input-number
          :value="topK"
          :min="1"
          :max="20"
          size="small"
          class="ctrl"
          @update:value="onTopK"
        />
      </div>

      <div class="row">
        <span class="label">Фильтр релевантности</span>
        <n-switch size="small" :value="relevanceEnabled" @update:value="onRelevanceEnabled" />
      </div>

      <div class="row">
        <span class="label">Кандидатов (до фильтра)</span>
        <n-input-number
          :value="retrieveTopK"
          :min="1"
          :max="50"
          size="small"
          class="ctrl"
          :disabled="!relevanceEnabled"
          @update:value="onRetrieveTopK"
        />
      </div>

      <div class="row">
        <span class="label">Порог отсечения</span>
        <n-input-number
          :value="relevanceThreshold"
          :min="0.0"
          :max="1.0"
          :step="0.05"
          size="small"
          class="ctrl"
          :disabled="!relevanceEnabled"
          @update:value="onRelevanceThreshold"
        />
      </div>

      <div class="status-row">
        <span class="status-dot" :style="{ background: status.color }" />
        <span class="status-text">{{ status.text }}</span>
        <n-button size="tiny" quaternary :disabled="knowledge.indexing" @click="rebuild">
          Переиндексировать
        </n-button>
      </div>

      <div v-if="knowledge.embedding_model" class="model">
        модель эмбедингов: <code>{{ knowledge.embedding_model }}</code>
        <span v-if="knowledge.indexing" class="model-progress">
          · обработано {{ knowledge.indexed }} из {{ knowledge.total }}
        </span>
      </div>
    </template>
  </div>
</template>

<style scoped>
.knowledge-panel {
  padding-top: 4px;
}
.hint {
  margin: 0 0 8px;
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  line-height: 1.5;
}
.hint code,
.model code {
  font-size: 11px;
}
.row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
  padding: 6px 0;
}
.label {
  font-size: 13px;
  color: var(--n-text-color, #ccc);
}
.ctrl {
  width: 160px;
}
.status-row {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 0 4px;
  border-top: 1px solid var(--n-border-color, #2a2e3a);
  margin-top: 8px;
}
.status-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex: none;
}
.status-text {
  font-size: 12px;
  color: var(--n-text-color-disabled, #888);
}
.status-row .n-button {
  margin-left: auto;
}
.model {
  font-size: 12px;
  color: var(--n-text-color-disabled, #888);
  margin-top: 6px;
}
.model-progress {
  opacity: 0.8;
}
.panel-error {
  color: #e05c5c;
  font-size: 12px;
  margin: 6px 0;
}
.panel-empty {
  color: var(--n-text-color-disabled, #666);
  font-size: 12px;
  padding: 8px 0;
}
</style>
