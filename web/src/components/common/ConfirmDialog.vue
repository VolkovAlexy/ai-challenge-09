<script setup lang="ts">
import { NModal, NButton, NSpace } from "naive-ui";

const props = defineProps<{ text: string }>();
const emit = defineEmits<{ answer: [ok: boolean] }>();
</script>

<template>
  <n-modal :show="true" preset="card" title="Подтверждение" style="width: 420px;" @close="emit('answer', false)">
    <div style="margin-bottom: 16px; color: #c8ccd4;">{{ props.text }}</div>
    <n-space justify="end">
      <n-button @click="emit('answer', false)">Отмена</n-button>
      <n-button type="error" @click="emit('answer', true)">Да</n-button>
    </n-space>
  </n-modal>
</template>