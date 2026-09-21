<script setup lang="ts">
// Композиция чата: лента + ввод + статус-бар + предложение памяти.
// Память/настройки — в правой панели (RightPanel) с кнопками-иконками.
import { computed } from "vue";
import { useAgentsStore } from "@/stores/agents";
import type { ChatCommand } from "@/commands/registry";
import MessageList from "./chat/MessageList.vue";
import ChatInput from "./chat/ChatInput.vue";
import StatusBar from "./chat/StatusBar.vue";
import MemorySuggestionBar from "./chat/MemorySuggestionBar.vue";
import RightPanel from "./chat/RightPanel.vue";

const props = defineProps<{
  commands: ChatCommand[];
  modelIds: () => string[];
  currentModel: string | null;
  onSend: (text: string) => Promise<void>;
  onStop: () => Promise<void>;
  onModelChange: (model: string) => void;
}>();

const store = useAgentsStore();

const agent = computed(() => store.activeAgent);
const streaming = computed(() => agent.value?.streaming === true);
</script>

<template>
  <div v-if="agent" class="chatview">
    <RightPanel>
      <div class="chat-col">
        <MessageList />
        <MemorySuggestionBar />
        <ChatInput
          :commands="props.commands"
          :streaming="streaming"
          :model-ids="props.modelIds"
          :current-model="props.currentModel"
          @send="props.onSend"
          @stop="props.onStop"
          @model-change="props.onModelChange"
        />
        <StatusBar />
      </div>
    </RightPanel>
  </div>
</template>

<style scoped>
.chatview {
  position: relative;
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.chat-col {
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  max-width: var(--chat-max-width);
  width: 100%;
  margin: 0 auto;
  overflow: hidden;
}
</style>
