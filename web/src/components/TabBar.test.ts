import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import TabBar from "./TabBar.vue";
import { useAgentsStore } from "@/stores/agents";

function seedStore(): void {
  const store = useAgentsStore();
  store.agents["a1"] = {
    id: "a1", name: "chat-1", model: "ollama:llama3.1",
    settings: { temperature: 0.7, top_p: 1, max_tokens: 1, stop: [] },
    systemPromptPath: "p", systemPromptContent: "s",
    history: [], streaming: true, compacting: false, cancelled: false, compactionNote: null,
    scratchpad: "", memorySuggestion: null, activeProfileId: "",
    task: null,
    invariants: [],
    streamingReasoning: "",
    subagents: [],
    sessionId: null, tokensIn: 0, tokensOut: 0, contextUsed: 0, contextWindow: 0,
  };
  store.agents["a2"] = {
    id: "a2", name: "chat-2", model: "openai:gpt-4",
    settings: { temperature: 0.7, top_p: 1, max_tokens: 1, stop: [] },
    systemPromptPath: "p", systemPromptContent: "s",
    history: [], streaming: false, compacting: false, cancelled: false, compactionNote: null,
    scratchpad: "", memorySuggestion: null, activeProfileId: "",
    task: null,
    invariants: [],
    streamingReasoning: "",
    subagents: [],
    sessionId: null,
    tokensIn: 0, tokensOut: 0, contextUsed: 0, contextWindow: 0,
  };
  store.order = ["a1", "a2"];
  store.activeAgentId = "a1";
}

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
  seedStore();
});

describe("TabBar", () => {
  it("активная вкладка помечена, названия видны", () => {
    const w = mount(TabBar);
    const tabs = w.findAll(".n-tabs-tab");
    expect(tabs.length).toBe(2);
    expect(tabs[0].classes()).toContain("n-tabs-tab--active");
    expect(tabs[1].text()).toContain("chat-2");
  });

  it("индикатор стрима только у стримящего агента", () => {
    const w = mount(TabBar);
    const tabs = w.findAll(".n-tabs-tab");
    expect(tabs[0].find(".tab-dot").exists()).toBe(true);
    expect(tabs[1].find(".tab-dot").exists()).toBe(false);
  });

  it("клик по вкладке меняет activeAgentId", async () => {
    const w = mount(TabBar);
    await w.findAll(".n-tabs-tab")[1].trigger("click");
    expect(useAgentsStore().activeAgentId).toBe("a2");
  });

  it("крестик эмитит closeTab", async () => {
    const w = mount(TabBar);
    await w.findAll(".tab-close")[1].trigger("click");
    expect(w.emitted("closeTab")?.[0]).toEqual(["a2"]);
  });

  it("кнопка «+» эмитит new", async () => {
    const w = mount(TabBar);
    await w.find(".tab-new").trigger("click");
    expect(w.emitted("new")).toHaveLength(1);
  });
});
