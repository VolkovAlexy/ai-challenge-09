// Общая настройка тестового окружения: localStorage + config @vue/test-utils.
import { config } from "@vue/test-utils";

// happy-dom в некоторых версиях не экспортирует localStorage в глобал — подстраховка.
if (typeof globalThis.localStorage === "undefined") {
  const store = new Map<string, string>();
  const storage = {
    getItem: (k: string): string | null => store.get(k) ?? null,
    setItem: (k: string, v: string): void => {
      store.set(k, v);
    },
    removeItem: (k: string): void => {
      store.delete(k);
    },
    clear: (): void => {
      store.clear();
    },
    key: (i: number): string | null => Array.from(store.keys())[i] ?? null,
    get length(): number {
      return store.size;
    },
  };
  Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true });
}

config.global.renderStubDefaultSlot = true;
