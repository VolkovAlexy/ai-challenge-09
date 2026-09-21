<script setup lang="ts">
import { computed, ref } from "vue";
import { NInput, NButton, NSelect } from "naive-ui";
import { completeInput } from "@/commands/registry";
import type { ChatCommand } from "@/commands/registry";

const props = defineProps<{
  commands: ChatCommand[];
  streaming: boolean;
  modelIds: () => string[];
  currentModel: string | null;
}>();

const emit = defineEmits<{
  send: [text: string];
  stop: [];
  modelChange: [model: string];
}>();

const text = ref("");
const menuIndex = ref(0);

const menuOpen = computed(() => menuItems.value.length > 0);

const modelOptions = computed(() =>
  props.modelIds().map((m) => ({ label: m, value: m })),
);

const menuItems = computed<string[]>(() => {
  const t = text.value;
  if (!t.startsWith("/")) return [];
  const parts = t.slice(1).split(/\s+/);
  if (parts.length === 1 && parts[0] !== "") {
    return props.commands
      .filter((c) => c.name.startsWith(parts[0].toLowerCase()))
      .map((c) => `/${c.name} ${c.args_spec}`.trim());
  }
  return [];
});

function submit(): void {
  const t = text.value.trim();
  if (t === "" || props.streaming) return;
  text.value = "";
  emit("send", t);
}

function onEnter(ev: KeyboardEvent): void {
  if (menuOpen.value && menuItems.value.length > 0) {
    applyVariant(menuItems.value[menuIndex.value]);
    ev.preventDefault();
    return;
  }
  if (ev.shiftKey) return;
  ev.preventDefault();
  submit();
}

function applyVariant(variant: string): void {
  const name = variant.split(/\s+/)[0];
  text.value = `${name} `;
}

function onKeydown(ev: KeyboardEvent): void {
  if (ev.key === "Enter") {
    onEnter(ev);
    return;
  }
  if (ev.key === "Tab") {
    const variants = completeInput(props.commands, { modelIds: props.modelIds }, text.value);
    if (variants.length > 0) {
      ev.preventDefault();
      text.value = `${variants[menuIndex.value % variants.length]} `;
      menuIndex.value += 1;
    }
    return;
  }
  if (ev.key === "ArrowDown" && menuOpen.value) {
    ev.preventDefault();
    menuIndex.value = (menuIndex.value + 1) % menuItems.value.length;
    return;
  }
  if (ev.key === "ArrowUp" && menuOpen.value) {
    ev.preventDefault();
    menuIndex.value = (menuIndex.value - 1 + menuItems.value.length) % menuItems.value.length;
    return;
  }
  if (ev.key === "Escape" && props.streaming) {
    ev.preventDefault();
    emit("stop");
  }
}
</script>

<template>
  <div class="chatinput">
    <div v-if="menuOpen" class="slash-menu">
      <div
        v-for="(item, i) in menuItems"
        :key="item"
        class="slash-item"
        :class="{ active: i === menuIndex }"
        @mousedown.prevent="applyVariant(item)"
      >
        {{ item }}
      </div>
    </div>
    <div class="chatinput-body">
      <n-input
        v-model:value="text"
        type="textarea"
        placeholder="Сообщение… (/ — команды, Shift+Enter — перенос)"
        :autosize="{ minRows: 3, maxRows: 10 }"
        :disabled="streaming"
        @keydown="onKeydown"
      />
      <div class="chatinput-foot">
        <div class="chatinput-foot-left">
          <n-select
            v-if="props.currentModel !== null"
            :value="props.currentModel"
            :options="modelOptions"
            size="small"
            placeholder="Модель"
            style="width: 200px;"
            @update:value="(v: string) => emit('modelChange', v)"
          />
        </div>
        <div class="chatinput-foot-right">
          <n-button v-if="streaming" type="error" size="small" @click="emit('stop')">
            Стоп
          </n-button>
          <n-button v-else type="primary" size="small" :disabled="text.trim() === ''" @click="submit">
            Отправить
          </n-button>
        </div>
      </div>
    </div>
  </div>
</template>