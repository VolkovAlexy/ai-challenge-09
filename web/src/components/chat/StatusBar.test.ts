import { beforeEach, describe, expect, it } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import StatusBar from "./StatusBar.vue";
import { useAgentsStore, type AgentState } from "@/stores/agents";
import { useConfigStore } from "@/stores/config";
import type { ConfigDTO } from "@/api/types";

function configDTO(threshold: number): ConfigDTO {
  return {
    providers: { ollama: { api_base: "http://x", models: { "llama3.1": {} } } },
    default_model: "ollama:llama3.1",
    temperature: 0.7,
    top_p: 1,
    max_tokens: 4096,
    stop: [],
    context_window_default: 32768,
    compaction_threshold: threshold,
    sliding_window: 20,
  };
}

function agentState(overrides: Partial<AgentState> = {}): AgentState {
  return {
    id: "a1",
    name: "chat-1",
    model: "ollama:llama3.1",
    settings: { temperature: 0.7, top_p: 1, max_tokens: 4096, stop: [] },
    systemPromptPath: "SYSTEM_PROMPT.md",
    systemPromptContent: "ты",
    history: [],
    streaming: false,
    compacting: false,
    cancelled: false,
    compactionNote: null,
    scratchpad: "",
    memorySuggestion: null,
    activeProfileId: "",
    sessionId: null,
    tokensIn: 5000,
    tokensOut: 1200,
    contextUsed: 12345,
    contextWindow: 32768,
    ...overrides,
  };
}

function seed(state: AgentState, threshold: number): void {
  const agents = useAgentsStore();
  useConfigStore().config = configDTO(threshold);
  agents.agents[state.id] = state;
  agents.activeAgentId = state.id;
}

beforeEach(() => {
  setActivePinia(createPinia());
});

describe("StatusBar", () => {
  it("формат строки: контекст, счётчики слева; T, top-p, max справа", () => {
    seed(agentState(), 0.6);
    const text = mount(StatusBar).text();
    expect(text).toContain("context");
    expect(text).toContain("T 0.7");
    expect(text).toContain("top-p 1");
    expect(text).toContain("max 4096");
    expect(text).toContain("12345/32768 (38%)");
    expect(text).toContain("in 5000 out 1200 Σ 6200");
  });

  it("жёлтый индикатор при превышении порога", () => {
    seed(agentState({ contextUsed: 90, contextWindow: 100 }), 0.6);
    expect(mount(StatusBar).find(".st-context").classes()).toContain("warn");
  });

  it("порог не превышен — без жёлтого", () => {
    seed(agentState({ contextUsed: 30, contextWindow: 100 }), 0.6);
    expect(mount(StatusBar).find(".st-context").classes()).not.toContain("warn");
  });

  it("индикатор состояния: стрим и сжатие", () => {
    seed(agentState({ streaming: true }), 0.6);
    expect(mount(StatusBar).text()).toContain("стрим");
    seed(agentState({ compacting: true }), 0.6);
    expect(mount(StatusBar).text()).toContain("сжимаю контекст");
  });
});
