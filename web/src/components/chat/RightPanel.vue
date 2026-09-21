<script setup lang="ts">
// Toolbar правой панели + сама панель: горизонтальная полоса вверху области чата,
// кнопки «Память» и «Настройки» — справа. Тело панели раскрывается справа колонной
// и отодвигает колонку сообщений (не перекрывает её). Заголовок панели — в её хедере.
import { ref } from "vue";
import { NButton, NScrollbar } from "naive-ui";
import MemoryPanel from "./MemoryPanel.vue";

type Tab = "memory" | "settings" | null;

const activeTab = ref<Tab>(null);

function toggle(tab: Exclude<Tab, null>): void {
  activeTab.value = activeTab.value === tab ? null : tab;
}
</script>

<template>
  <div class="right-panel">
    <div class="rp-bar">
      <div class="rp-actions">
        <n-button
          class="rp-bar-btn"
          :class="{ active: activeTab === 'memory' }"
          quaternary
          title="Память"
          @click="toggle('memory')"
        >
          <span class="rp-bar-icon" aria-hidden="true">🧠</span>
        </n-button>
        <n-button
          class="rp-bar-btn"
          :class="{ active: activeTab === 'settings' }"
          quaternary
          title="Настройки"
          @click="toggle('settings')"
        >
          <span class="rp-bar-icon" aria-hidden="true">⚙️</span>
        </n-button>
      </div>
    </div>
    <div class="chat-main">
      <slot />
      <transition name="rp-slide">
        <div v-if="activeTab !== null" class="rp-body">
          <div class="rp-header">
            <span class="rp-title">
              {{ activeTab === "memory" ? "Память" : "Настройки" }}
            </span>
          </div>
          <n-scrollbar class="rp-body-scroll">
            <div class="rp-body-content">
              <MemoryPanel v-if="activeTab === 'memory'" :active="true" />
              <div v-else class="rp-placeholder">Настройки — следующая итерация</div>
            </div>
          </n-scrollbar>
        </div>
      </transition>
    </div>
  </div>
</template>

<style scoped>
.right-panel {
  flex: 1;
  min-height: 0;
  min-width: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
}

.rp-bar {
  display: flex;
  align-items: center;
  justify-content: flex-end;
  gap: 8px;
  height: 42px;
  padding: 0 16px;
  background: #16171d;
  border-bottom: 1px solid #1e2030;
}

.rp-actions {
  display: flex;
  align-items: center;
  gap: 4px;
}

.rp-bar-btn {
  width: 28px !important;
  height: 28px !important;
  padding: 0 !important;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 4px;
}

.rp-bar-btn.active {
  color: #7aa2f7;
  background: #1c212c;
}

.rp-bar-icon {
  font-size: 15px;
  line-height: 1;
}

.chat-main {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: row;
  overflow: hidden;
}

.rp-body {
  flex: 0 0 340px;
  width: 340px;
  background: #1a1d25;
  border-left: 1px solid #2a2e3a;
  border-bottom: 1px solid #2a2e3a;
  display: flex;
  flex-direction: column;
  min-height: 0;
}

.rp-header {
  display: flex;
  align-items: center;
  padding: 8px 16px;
  border-bottom: 1px solid #1e2030;
}

.rp-title {
  font-size: 13px;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: #777;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.rp-body-scroll {
  flex: 1;
  min-height: 0;
}

.rp-body-content {
  padding: 12px 16px;
}

.rp-placeholder {
  color: #666;
  font-size: 13px;
  font-style: italic;
  padding: 8px 0;
}

.rp-slide-enter-active,
.rp-slide-leave-active {
  transition: opacity 0.16s ease;
}

.rp-slide-enter-from,
.rp-slide-leave-to {
  opacity: 0;
}
</style>
