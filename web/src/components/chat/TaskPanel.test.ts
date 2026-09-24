import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import TaskPanel from "./TaskPanel.vue";
import { useAgentsStore, type AgentState } from "@/stores/agents";
import type { TaskStateDTO } from "@/api/types";

function taskDTO(overrides: Partial<TaskStateDTO> = {}): TaskStateDTO {
  return {
    phase: "planning",
    step: 1,
    steps: [],
    validation_steps: [],
    expected_action: "составить план",
    description: "задача",
    paused: false,
    plan_confirmed: false,
    ...overrides,
  };
}

function agentState(overrides: Partial<AgentState> = {}): AgentState {
  return {
    id: "a1",
    name: "chat-1",
    model: "ollama:llama3.1",
    settings: { temperature: 0.7, top_p: 1, max_tokens: 4096, stop: [], context_strategy: "summary", sliding_window: 256, compaction_threshold: 0.6 },
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
    task: null,
    invariants: [],
    streamingReasoning: "",
    subagents: [],
    sessionId: null,
    tokensIn: 0,
    tokensOut: 0,
    contextUsed: 0,
    contextWindow: 32768,
    ...overrides,
  };
}

function seed(state: AgentState): void {
  const agents = useAgentsStore();
  agents.agents[state.id] = state;
  agents.activeAgentId = state.id;
}

beforeEach(() => {
  setActivePinia(createPinia());
});

function mountPanel() {
  return mount(TaskPanel, {
    global: {
      stubs: {
        NButton: {
          template: '<button @click="$emit(\'click\')"><slot/></button>',
          props: ["type", "size", "loading", "disabled", "quaternary"],
          emits: ["click"],
        },
        NInput: {
          template: '<input :value="value" @input="$emit(\'update:value\', $event.target.value)"/>',
          props: ["value", "placeholder", "size", "type"],
          emits: ["update:value"],
        },
        NTag: {
          template: "<span><slot/></span>",
          props: ["type", "size", "bordered"],
        },
      },
    },
  });
}

describe("TaskPanel", () => {
  it("в фазе «готово» шаги не отображаются (только финальный результат)", () => {
    seed(
      agentState({
        task: taskDTO({
          phase: "done",
          step: 3,
          steps: ["п1", "п2", "п3"],
          validation_steps: ["в1", "в2", "в3"],
        }),
      }),
    );
    const w = mountPanel();
    expect(w.findAll(".task-step-current").length).toBe(0);
    expect(w.findAll("ul.task-steps").length).toBe(0);
    expect(w.text()).toContain("готово");
  });

  it("нет свободного селекта этапа и кнопки «Сменить этап»", () => {
    seed(
      agentState({
        task: taskDTO({ phase: "execution", step: 1, steps: ["а", "б"] }),
      }),
    );
    const w = mountPanel();
    expect(w.text()).not.toContain("Сменить этап");
    expect(w.find(".task-phase-select").exists()).toBe(false);
  });

  it("на последнем шаге выполнения кнопка переводит в проверку", async () => {
    seed(
      agentState({
        task: taskDTO({
          phase: "execution",
          step: 2,
          steps: ["а", "б"],
          validation_steps: ["в"],
        }),
      }),
    );
    const store = useAgentsStore();
    const spy = vi.spyOn(store, "setTaskPhase").mockResolvedValue();
    const w = mountPanel();
    const btn = w.findAll("button").find((b) => b.text().includes("Завершить и проверить"));
    expect(btn).toBeTruthy();
    await btn!.trigger("click");
    expect(spy).toHaveBeenCalledWith("a1", "validation", expect.any(String));
  });

  it("на промежуточном шаге кнопка продвигает шаг (advance)", async () => {
    seed(
      agentState({
        task: taskDTO({ phase: "execution", step: 1, steps: ["а", "б"] }),
      }),
    );
    const store = useAgentsStore();
    const spy = vi.spyOn(store, "advanceTaskStep").mockResolvedValue();
    const w = mountPanel();
    const btn = w.findAll("button").find((b) => b.text().includes("Шаг выполнен (дальше)"));
    expect(btn).toBeTruthy();
    await btn!.trigger("click");
    expect(spy).toHaveBeenCalledWith("a1");
  });
});
