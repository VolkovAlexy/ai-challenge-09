<script setup lang="ts">
import { computed, ref } from "vue";
import { NModal, NInput, NList, NListItem, NScrollbar } from "naive-ui";
import { useConfigStore } from "@/stores/config";

const props = defineProps<{ current: string | null }>();
const emit = defineEmits<{ select: [value: string | null] }>();

const configStore = useConfigStore();
const filter = ref("");

const items = computed(() => {
  const f = filter.value.toLowerCase();
  return configStore
    .allModelIds()
    .filter((m) => f === "" || m.toLowerCase().includes(f))
    .map((m) => ({ value: m, label: m }));
});
</script>

<template>
  <n-modal :show="true" preset="card" title="Выбор модели" style="width: 480px;" @close="emit('select', null)">
    <n-input v-model:value="filter" placeholder="Фильтр моделей…" style="margin-bottom: 12px;" />
    <n-scrollbar style="max-height: 320px;">
      <n-list hoverable clickable>
        <n-list-item
          v-for="item in items"
          :key="item.value"
          @click="emit('select', item.value)"
        >
          <template #prefix>
            <span v-if="item.value === props.current" style="color: #7aa2f7;">●</span>
            <span v-else style="color: transparent;">●</span>
          </template>
          {{ item.label }}
        </n-list-item>
      </n-list>
    </n-scrollbar>
  </n-modal>
</template>