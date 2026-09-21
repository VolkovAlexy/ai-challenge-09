<script setup lang="ts">
import { NTabs, NTab } from "naive-ui";
import { useAgentsStore } from "@/stores/agents";

const store = useAgentsStore();

const emit = defineEmits<{ new: []; closeTab: [id: string] }>();
</script>

<template>
  <div class="tabbar" role="tablist">
    <n-tabs
      :value="store.activeAgentId ?? ''"
      type="card"
      size="small"
      tab-style="min-width: 0;"
      @update:value="(v: string) => v !== '' && store.setActive(v)"
    >
      <n-tab
        v-for="id in store.order"
        :key="id"
        :name="id"
      >
        <template #default>
          <span class="tab-content">
            <span class="tab-name">{{ store.agents[id]?.name }}</span>
            <span v-if="store.agents[id]?.streaming" class="tab-dot" title="идёт стрим" />
            <span
              class="tab-close"
              title="Закрыть вкладку"
              @click.stop="store.setActive(id); emit('closeTab', id)"
            >×</span>
          </span>
        </template>
      </n-tab>
    </n-tabs>
    <button class="tab-new" title="Новый чат" @click="emit('new')">+</button>
  </div>
</template>

<style scoped>
.tabbar {
  display: flex;
  align-items: center;
  background: #16171d;
  border-bottom: 1px solid #1e2030;
  padding-right: 8px;
}

.tabbar :deep(.n-tabs-nav) {
  background: transparent;
  border-bottom: none;
}

.tabbar :deep(.n-tabs-tab) {
  background: transparent;
  border: none;
  color: #666;
}

.tabbar :deep(.n-tabs-tab--active) {
  background: #101014;
  color: #c8ccd4;
  border-color: #1e2030;
}

.tab-content {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
}

.tab-name {
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tab-dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: #9ece6a;
  flex-shrink: 0;
}

.tab-close {
  background: none;
  border: none;
  color: #555;
  font-size: 14px;
  cursor: pointer;
  padding: 0 2px;
  line-height: 1;
  border-radius: 4px;
  margin-left: 4px;
}

.tab-close:hover {
  color: #f7768e;
  background: rgba(247, 118, 142, 0.1);
}

.tab-new {
  background: none;
  border: none;
  color: #555;
  font-size: 16px;
  cursor: pointer;
  padding: 4px 8px;
  border-radius: 4px;
  flex-shrink: 0;
}

.tab-new:hover {
  color: #7aa2f7;
  background: #1c212c;
}
</style>