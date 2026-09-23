import { describe, expect, it } from "vitest";
import { mount } from "@vue/test-utils";
import MessageItem from "./MessageItem.vue";
import type { MessageDTO } from "@/api/types";

describe("MessageItem", () => {
  it("markdown рендерится для ассистента: жирный, список, код", () => {
    const m: MessageDTO = {
      id: "1",
      role: "assistant",
      content: "**Жирный** и `код`\n\n- пункт\n\n```python\nprint('привет')\n```",
    };
    const w = mount(MessageItem, { props: { message: m } });
    expect(w.find(".md strong").text()).toBe("Жирный");
    expect(w.find(".md code").exists()).toBe(true);
    expect(w.find(".md li").exists()).toBe(true);
    expect(w.find(".md pre").exists()).toBe(true);
  });

  it("XSS вырезается DOMPurify", () => {
    const m: MessageDTO = { id: "1", role: "assistant", content: '<script>alert(1)</script>ok' };
    const w = mount(MessageItem, { props: { message: m } });
    expect(w.html()).not.toContain("<script");
    expect(w.find(".md").text()).toContain("ok");
  });

  it("пользовательский ввод — plain text с escape", () => {
    const m: MessageDTO = { id: "2", role: "user", content: "<b>не жирный</b>" };
    const w = mount(MessageItem, { props: { message: m } });
    expect(w.find(".msg-user .msg-body").text()).toBe("<b>не жирный</b>");
  });

  it("footer токенов: in/out, think только при размышлениях, ~ при approx", () => {
    const base: MessageDTO = {
      id: "3",
      role: "assistant",
      content: "ответ",
      usage: { prompt_tokens: 100, completion_tokens: 50 },
    };
    expect(mount(MessageItem, { props: { message: base } }).find(".msg-tokens").text()).toBe(
      "in 100 · out 50",
    );
    const think: MessageDTO = {
      ...base,
      usage: { prompt_tokens: 100, completion_tokens: 60, reasoning_tokens: 10 },
    };
    expect(mount(MessageItem, { props: { message: think } }).find(".msg-tokens").text()).toContain("think 10");
    const approx: MessageDTO = {
      ...base,
      usage: { prompt_tokens: 100, completion_tokens: 50, approx: true },
    };
    expect(mount(MessageItem, { props: { message: approx } }).find(".msg-tokens").text()).toBe(
      "in ~100 · out ~50",
    );
  });

  it("блок размышлений: свёрнут — последние 3 строки, клик раскрывает всё", async () => {
    const m: MessageDTO = {
      id: "5",
      role: "assistant",
      content: "ответ",
      reasoning: "строка 1\nстрока 2\nстрока 3\nстрока 4\nстрока 5",
    };
    const w = mount(MessageItem, { props: { message: m } });
    const block = w.find(".msg-reasoning");
    expect(block.exists()).toBe(true);
    expect(block.find(".msg-reasoning-preview").text()).toBe("строка 3\nстрока 4\nстрока 5");
    expect((block.find(".msg-reasoning-body").element as HTMLElement).style.display).toBe("none");
    await block.trigger("click");
    expect(block.find(".msg-reasoning-body").text()).toBe(
      "строка 1\nстрока 2\nстрока 3\nстрока 4\nстрока 5",
    );
    expect(
      (block.find(".msg-reasoning-body").element as HTMLElement).style.display,
    ).not.toBe("none");
  });

  it("спиннер думания — только у живого размышления без контента", () => {
    const m: MessageDTO = { id: "6", role: "assistant", content: "", reasoning: "думаю" };
    const w = mount(MessageItem, { props: { message: m, live: true } });
    expect(w.find(".msg-reasoning-spinner").exists()).toBe(true);
    const w2 = mount(MessageItem, { props: { message: { ...m, content: "ответ" }, live: true } });
    expect(w2.find(".msg-reasoning-spinner").exists()).toBe(false);
  });

  it("без размышлений блок не рендерится", () => {
    const m: MessageDTO = { id: "7", role: "assistant", content: "ответ" };
    const w = mount(MessageItem, { props: { message: m } });
    expect(w.find(".msg-reasoning").exists()).toBe(false);
  });

  it("результат инструмента: свёрнут — первые 3 строки, клик раскрывает всё", async () => {
    const m: MessageDTO = {
      id: "8",
      role: "tool",
      tool_name: "browser_navigate",
      content: "строка 1\nстрока 2\nстрока 3\nстрока 4\nстрока 5",
    };
    const w = mount(MessageItem, { props: { message: m } });
    const block = w.find(".msg-note.msg-tool");
    expect(block.exists()).toBe(true);
    expect(block.find(".msg-tool-body").text()).toBe("🔧 browser_navigate: строка 1\nстрока 2\nстрока 3\n…");
    await block.trigger("click");
    expect(block.find(".msg-tool-body").text()).toBe(
      "🔧 browser_navigate: строка 1\nстрока 2\nстрока 3\nстрока 4\nстрока 5",
    );
  });

  it("короткий результат инструмента не свернут и не сворачивается", async () => {
    const m: MessageDTO = {
      id: "9",
      role: "tool",
      tool_name: "echo",
      content: "одна строка",
    };
    const w = mount(MessageItem, { props: { message: m } });
    const block = w.find(".msg-note.msg-tool");
    expect(block.find(".msg-tool-caret").exists()).toBe(false);
    expect(block.find(".msg-tool-body").text()).toBe("🔧 echo: одна строка");
    await block.trigger("click");
    expect(block.find(".msg-tool-body").text()).toBe("🔧 echo: одна строка");
  });

  it("ошибка рендерится отдельным стилем", () => {
    const m: MessageDTO = {
      id: "4",
      role: "system",
      content: "Unauthorized",
      error: { kind: "http", detail: "Unauthorized" },
    };
    const w = mount(MessageItem, { props: { message: m } });
    expect(w.find(".msg-error").text()).toBe("Unauthorized");
  });
});
