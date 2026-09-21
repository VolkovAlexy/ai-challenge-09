<script setup lang="ts">
import { computed, ref } from "vue";
import { NModal, NInput, NList, NListItem, NScrollbar } from "naive-ui";
import type { ChatCommand } from "@/commands/registry";

const props = defineProps<{ commands: ChatCommand[] }>();
const emit = defineEmits<{ select: [value: string | null] }>();

const filter = ref("");

const items = computed(() => {
  const f = filter.value.toLowerCase();
  return props.commands
    .filter((c) => f === "" || c.name.includes(f) || c.description.toLowerCase().includes(f))
    .map((c) => ({
      value: `/${c.name}`,
      label: `/${c.name} ${c.args_spec}`.trim(),
      hint: c.description,
    }));
});
</script>

<template>
  <n-modal :show="true" preset="card" title="Команды" style="width: 520px;" @close="emit('select', null)">
    <n-input v-model:value="filter" placeholder="Фильтр команд…" style="margin-bottom: 12px;" />
    <n-scrollbar style="max-height: 320px;">
      <n-list hoverable clickable>
        <n-list-item v-for="item in items" :key="item.value" @click="emit('select', item.value)">
          <template #default>
            <div>
              <div style="font-family: ui-monospace, monospace; font-size: 13px;">{{ item.label }}</div>
              <div style="font-size: 12px; color: #666;">{{ item.hint }}</div>
            </div>
          </template>
        </n-list-item>
      </n-list>
    </n-scrollbar>
  </n-modal>
</template>