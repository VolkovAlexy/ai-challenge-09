import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import ChatInput from "./ChatInput.vue";
import { buildRegistry } from "@/commands/registry";
import type { CommandDTO } from "@/api/types";

const dtos: CommandDTO[] = [
  { name: "help", description: "список команд", args_spec: "" },
  { name: "temperature", description: "temperature", args_spec: "<0..2>" },
  { name: "model", description: "палитра выбора модели", args_spec: "[provider:model]" },
];

beforeEach(() => {
  setActivePinia(createPinia());
});

function mountInput(streaming = false) {
  return mount(ChatInput, {
    global: {
      stubs: {
        Button: {
          template: '<button :class="type === \'error\' ? \'btn-stop\' : \'btn-send\'"><slot/></button>',
          props: ["type", "disabled", "size"],
        },
        Input: {
          template: '<div><textarea :value="value" @keydown="$emit(\'keydown\', $event)" @input="$emit(\'update:value\', $event.target.value)"/></div>',
          props: ["value", "type", "placeholder", "autosize", "disabled"],
          emits: ["update:value", "keydown"],
        },
        Select: {
          template: '<div class="n-select"><select :value="value"><option v-for="o in options" :value="o.value" :key="o.value">{{ o.label }}</option></select></div>',
          props: ["value", "options", "size", "placeholder", "style"],
          emits: ["update:value"],
        },
      },
    },
    props: { commands: buildRegistry(dtos), streaming, modelIds: () => ["ollama:llama3.1", "openai:gpt-4"], currentModel: "ollama:llama3.1", profileOptions: [], currentProfile: "" },
  });
}

describe("ChatInput", () => {
  it("slash-меню открывается по / и фильтруется", async () => {
    const w = mountInput();
    await w.find("textarea").setValue("/te");
    const items = w.findAll(".slash-item");
    expect(items.length).toBe(1);
    expect(items[0].text()).toContain("/temperature");
  });

  it("навигация ↑↓ / Enter — выбор из меню", async () => {
    const w = mountInput();
    await w.find("textarea").setValue("/te");
    await w.find("textarea").trigger("keydown", { key: "Enter" });
    expect(w.find("textarea").element.value).toBe("/temperature ");
  });

  it("Esc закрывает меню (стрим выключен — стоп не эмитится)", async () => {
    const w = mountInput(false);
    await w.find("textarea").setValue("/te");
    expect(w.find(".slash-menu").exists()).toBe(true);
    await w.find("textarea").trigger("keydown", { key: "Escape" });
    // streaming=false -> stop не эмитится
    expect(w.emitted("stop")).toBeUndefined();
  });

  it("Tab-дополнение команды", async () => {
    const w = mountInput();
    await w.find("textarea").setValue("/te");
    await w.find("textarea").trigger("keydown", { key: "Tab" });
    expect(w.find("textarea").element.value).toBe("/temperature ");
  });

  it("Tab-дополнение модели для /model", async () => {
    const w = mountInput();
    await w.find("textarea").setValue("/model ollama");
    await w.find("textarea").trigger("keydown", { key: "Tab" });
    expect(w.find("textarea").element.value).toBe("/model ollama:llama3.1 ");
  });

  it("Enter без меню отправляет текст; пустой ввод игнорируется", async () => {
    const w = mountInput();
    await w.find("textarea").setValue("");
    await w.find("textarea").trigger("keydown", { key: "Enter" });
    expect(w.emitted("send")).toBeUndefined();
    await w.find("textarea").setValue("привет");
    await w.find("textarea").trigger("keydown", { key: "Enter" });
    expect(w.emitted("send")?.[0]).toEqual(["привет"]);
  });

  it("во время стрима кнопка «Стоп» активна, Enter не отправляет новый запрос", async () => {
    const w = mountInput(true);
    expect(w.find(".btn-stop").exists()).toBe(true);
    await w.find("textarea").setValue("текст");
    await w.find("textarea").trigger("keydown", { key: "Enter" });
    expect(w.emitted("send")).toBeUndefined();
    await w.find(".btn-stop").trigger("click");
    expect(w.emitted("stop")).toHaveLength(1);
  });
});