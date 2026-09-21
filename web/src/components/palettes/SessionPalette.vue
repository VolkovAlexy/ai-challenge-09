<script setup lang="ts">
import { computed, ref } from "vue";
import { NModal, NInput, NList, NListItem, NScrollbar } from "naive-ui";
import { useSessionsStore } from "@/stores/sessions";

const emit = defineEmits<{ select: [value: string | null] }>();

const sessionsStore = useSessionsStore();
void sessionsStore.load();
const filter = ref("");

const items = computed(() => {
  const f = filter.value.toLowerCase();
  return sessionsStore.sessions
    .filter((s) => f === "" || s.title.toLowerCase().includes(f) || s.id.toLowerCase().includes(f))
    .map((s) => ({
      value: s.id,
      label: s.title,
      hint: new Date(s.updated_at).toLocaleString("ru-RU"),
    }));
});
</script>

<template>
  <n-modal :show="true" preset="card" title="Сессии" style="width: 480px;" @close="emit('select', null)">
    <n-input v-model:value="filter" placeholder="Фильтр сессий…" style="margin-bottom: 12px;" />
    <n-scrollbar style="max-height: 340px;">
      <n-list hoverable clickable>
        <n-list-item v-for="item in items" :key="item.value" @click="emit('select', item.value)">
          <template #default>
            <div>
              <div style="font-size: 13px;">{{ item.label }}</div>
              <div style="font-size: 11px; color: #666;">{{ item.hint }}</div>
            </div>
          </template>
        </n-list-item>
      </n-list>
    </n-scrollbar>
  </n-modal>
</template>