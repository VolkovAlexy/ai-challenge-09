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
    expect(w.find(".msg-user").text()).toBe("<b>не жирный</b>");
  });

  it("footer токенов: in/out, think только при размышлениях, ~ при approx", () => {
    const base: MessageDTO = {
      id: "3",
      role: "assistant",
      content: "ответ",
      usage: { prompt_tokens: 100, completion_tokens: 50 },
    };
    expect(mount(MessageItem, { props: { message: base } }).find(".msg-tokens").text()).toBe(
      "tokens: in 100 · out 50",
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
      "tokens: in ~100 · out ~50",
    );
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
