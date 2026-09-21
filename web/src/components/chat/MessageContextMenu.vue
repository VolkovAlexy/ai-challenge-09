<script setup lang="ts">
// Контекстное меню сообщения: выпадающий список действий (branch/remember/copy).
// Позиционируется по координатам кнопки ⋮; закрывается по клику вне и по Escape.
import { computed, onBeforeUnmount, onMounted } from "vue";

export interface MenuItem {
  id: string;
  label: string;
  danger?: boolean;
}

const props = defineProps<{ x: number; y: number; items: MenuItem[] }>();
const emit = defineEmits<{ select: [id: string]; close: [] }>();

const style = computed(() => ({ left: `${props.x}px`, top: `${props.y}px` }));

function onDocPointerDown(event: PointerEvent): void {
  const target = event.target as HTMLElement | null;
  if (target?.closest(".ctx-menu") !== null) return;
  emit("close");
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape") emit("close");
}

function select(id: string): void {
  emit("select", id);
  emit("close");
}

onMounted(() => {
  document.addEventListener("pointerdown", onDocPointerDown, true);
  document.addEventListener("keydown", onKeydown);
});
onBeforeUnmount(() => {
  document.removeEventListener("pointerdown", onDocPointerDown, true);
  document.removeEventListener("keydown", onKeydown);
});
</script>

<template>
  <div class="ctx-menu" :style="style" role="menu">
    <button
      v-for="item in items"
      :key="item.id"
      class="ctx-menu-item"
      :class="{ danger: item.danger === true }"
      role="menuitem"
      type="button"
      @click="select(item.id)"
    >
      {{ item.label }}
    </button>
  </div>
</template>

<style scoped>
.ctx-menu {
  position: fixed;
  z-index: 1000;
  min-width: 180px;
  padding: 4px;
  background: var(--n-color, #26262a);
  border: 1px solid var(--n-border-color, #3a3a3f);
  border-radius: 8px;
  box-shadow: 0 8px 24px rgba(0, 0, 0, 0.4);
  z-index: 1000;
}
.ctx-menu-item {
  display: block;
  width: 100%;
  padding: 6px 10px;
  border: 0;
  border-radius: 6px;
  background: transparent;
  color: inherit;
  font-size: 13px;
  text-align: left;
  cursor: pointer;
}
.ctx-menu-item:hover {
  background: var(--n-action-color, #333338);
}
.ctx-menu-item.danger {
  color: #e88080;
}
</style>
