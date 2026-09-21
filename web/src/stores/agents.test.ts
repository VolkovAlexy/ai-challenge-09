import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useAgentsStore } from "./agents";
import type { AgentDTO, MessageDTO, StreamEvent } from "@/api/types";

let nextId = 0;

function agentDTO(overrides: Partial<AgentDTO> = {}): AgentDTO {
  nextId += 1;
  return {
    id: `a${nextId}`,
    name: `chat-${nextId}`,
    model: "ollama:m1",
    settings: { temperature: 0.7, top_p: 1, max_tokens: 100, stop: [] },
    system_prompt: { path: "SYSTEM_PROMPT.md", content: "ты" },
    context_used: 10,
    context_window: 1000,
    streaming: false,
    compacting: false,
    scratchpad: "",
    memory_suggestion: null,
    ...overrides,
  };
}

function mockFetch(handlers: Map<string, (body?: unknown) => Response>): void {
  vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
      const key = `${init?.method ?? "GET"} ${String(url).replace(/^\/api/, "")}`;
    const h = handlers.get(key);
    if (h === undefined) throw new Error(`нет заглушки для ${key}`);
    return h(init?.body === undefined ? undefined : JSON.parse(String(init.body)));
  }));
}

function sseResponse(events: StreamEvent[]): Response {
  const text = events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join("");
  return new Response(text, { status: 200, headers: { "Content-Type": "text/event-stream" } });
}

beforeEach(() => {
  setActivePinia(createPinia());
  localStorage.clear();
  nextId = 0;
  vi.unstubAllGlobals();
});

describe("agents store", () => {
  it("создание агента добавляет его и делает активным", async () => {
    const dto = agentDTO();
    mockFetch(new Map([["POST /agents", () => new Response(JSON.stringify(dto), { status: 200 })]]));
    const store = useAgentsStore();
    const state = await store.createAgent();
    expect(state.id).toBe(dto.id);
    expect(store.activeAgentId).toBe(dto.id);
    expect(store.order).toEqual([dto.id]);
    expect(localStorage.getItem("my-agent.activeAgentId")).toBe(dto.id);
  });

  it("закрытие агента удаляет его; активным становится сосед", async () => {
    const d1 = agentDTO();
    const d2 = agentDTO();
    const handlers = new Map<string, (body?: unknown) => Response>([
      ["POST /agents", () => new Response(JSON.stringify(d1), { status: 200 })],
    ]);
    mockFetch(handlers);
    const store = useAgentsStore();
    await store.createAgent();
    handlers.set("POST /agents", () => new Response(JSON.stringify(d2), { status: 200 }));
    await store.createAgent();
    expect(store.order).toEqual([d1.id, d2.id]);
    handlers.set(`DELETE /agents/${d2.id}`, () => new Response("{}", { status: 200 }));
    await store.closeAgent(d2.id);
    expect(store.order).toEqual([d1.id]);
    expect(store.agents[d2.id]).toBeUndefined();
    expect(store.activeAgentId).toBe(d1.id);
  });

  it("история загружается в loadAll (восстановление после перезагрузки)", async () => {
    const dto = agentDTO();
    const history: MessageDTO[] = [
      { id: "m1", role: "user", content: "привет" },
      { id: "m2", role: "assistant", content: "привет!", usage: { prompt_tokens: 5, completion_tokens: 3 } },
    ];
    mockFetch(
      new Map([
        ["GET /agents", () => new Response(JSON.stringify([dto]), { status: 200 })],
        [`GET /agents/${dto.id}/messages`, () => new Response(JSON.stringify(history), { status: 200 })],
      ]),
    );
    const store = useAgentsStore();
    await store.loadAll();
    expect(store.agents[dto.id]?.history).toEqual(history);
    expect(store.activeAgentId).toBe(dto.id);
  });

  it("usage из done начисляется в счётчики агента", async () => {
    const dto = agentDTO();
    const finalMsg: MessageDTO = {
      id: "m2",
      role: "assistant",
      content: "ответ",
      usage: { prompt_tokens: 12, completion_tokens: 7, reasoning_tokens: 2 },
    };
    mockFetch(
      new Map([
        ["POST /agents", () => new Response(JSON.stringify(dto), { status: 200 })],
        [
          `POST /agents/${dto.id}/messages`,
          () =>
            sseResponse([
              { event: "user_message", message: { id: "m1", role: "user", content: "вопрос" } },
              { event: "done", message: finalMsg },
            ]),
        ],
      ]),
    );
    const store = useAgentsStore();
    await store.createAgent();
    await store.runStream(dto.id, "вопрос");
    const state = store.agents[dto.id];
    expect(state?.tokensIn).toBe(12);
    expect(state?.tokensOut).toBe(7);
    expect(state?.history.map((m) => m.id)).toEqual(["m1", "m2"]);
    expect(state?.streaming).toBe(false);
    expect(state?.contextUsed).toBe(12);
  });

  it("дельты копятся в стрим-сообщение; done заменяет его финальным", async () => {
    const dto = agentDTO();
    mockFetch(
      new Map([
        ["POST /agents", () => new Response(JSON.stringify(dto), { status: 200 })],
        [
          `POST /agents/${dto.id}/messages`,
          () =>
            sseResponse([
              { event: "user_message", message: { id: "m1", role: "user", content: "в" } },
              { event: "delta", content: "От" },
              { event: "delta", content: "вет" },
              { event: "done", message: { id: "m2", role: "assistant", content: "Ответ" } },
            ]),
        ],
      ]),
    );
    const store = useAgentsStore();
    await store.createAgent();
    await store.runStream(dto.id, "в");
    const state = store.agents[dto.id];
    expect(state?.history).toHaveLength(2);
    expect(state?.history[1]).toEqual({ id: "m2", role: "assistant", content: "Ответ" });
  });

  it("reasoning_delta копится в стрим-сообщение и переносится в done", async () => {
    const dto = agentDTO();
    mockFetch(
      new Map([
        ["POST /agents", () => new Response(JSON.stringify(dto), { status: 200 })],
        [
          `POST /agents/${dto.id}/messages`,
          () =>
            sseResponse([
              { event: "user_message", message: { id: "m1", role: "user", content: "в" } },
              { event: "reasoning_delta", content: "думаю" },
              { event: "reasoning_delta", content: " ещё" },
              { event: "delta", content: "Ответ" },
              { event: "done", message: { id: "m2", role: "assistant", content: "Ответ", reasoning: "думаю ещё" } },
            ]),
        ],
      ]),
    );
    const store = useAgentsStore();
    await store.createAgent();
    await store.runStream(dto.id, "в");
    const state = store.agents[dto.id];
    expect(state?.history[1]?.reasoning).toBe("думаю ещё");
    expect(state?.streamingReasoning).toBe("");
  });

  it("compaction events выставляют compacting и заметку", async () => {
    const dto = agentDTO();
    const events: StreamEvent[] = [
      { event: "user_message", message: { id: "m1", role: "user", content: "х" } },
      { event: "compaction_started" },
      { event: "compaction_done", removed: 42, summary_tokens: 7, pct_before: 0.9, pct_after: 0.25 },
      { event: "done", message: { id: "m2", role: "assistant", content: "ок" } },
    ];
    mockFetch(
      new Map([
        ["POST /agents", () => new Response(JSON.stringify(dto), { status: 200 })],
        [`POST /agents/${dto.id}/messages`, () => sseResponse(events)],
      ]),
    );
    const store = useAgentsStore();
    await store.createAgent();
    await store.runStream(dto.id, "х");
    const state = store.agents[dto.id];
    expect(state?.compacting).toBe(false);
    expect(state?.compactionNote).toBe("⇄ Контекст сжат: -42 удалено, +7 саммари (90% → 25%)");
  });

  it("error-событие попадает в историю как system с error, стрим завершается", async () => {
    const dto = agentDTO();
    mockFetch(
      new Map([
        ["POST /agents", () => new Response(JSON.stringify(dto), { status: 200 })],
        [
          `POST /agents/${dto.id}/messages`,
          () =>
            sseResponse([
              { event: "user_message", message: { id: "m1", role: "user", content: "х" } },
              { event: "error", kind: "http", detail: "Unauthorized" },
            ]),
        ],
      ]),
    );
    const store = useAgentsStore();
    await store.createAgent();
    await store.runStream(dto.id, "х");
    const state = store.agents[dto.id];
    expect(state?.history[1]?.error).toEqual({ kind: "http", detail: "Unauthorized" });
    expect(state?.streaming).toBe(false);
  });

  it("параллельные стримы двух агентов не путают историю", async () => {
    const d1 = agentDTO();
    const d2 = agentDTO();
    let created = 0;
    const sse = (prefix: string) =>
      sseResponse([
        { event: "user_message", message: { id: "u", role: "user", content: `${prefix}q` } },
        { event: "delta", content: prefix },
        { event: "done", message: { id: "d", role: "assistant", content: prefix } },
      ]);
    vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
      const u = String(url);
      if (u === "/api/agents" && (init?.method ?? "GET") === "POST") {
        created += 1;
        return new Response(JSON.stringify(created === 1 ? d1 : d2), { status: 200 });
      }
      if (u.endsWith("/messages") && init?.method === "POST") {
        const content = (JSON.parse(String(init.body)) as { content: string }).content;
        return sse(content === "q1" ? "1" : "2");
      }
      throw new Error(`нет заглушки ${u}`);
    }));
    const store = useAgentsStore();
    await store.createAgent(); // d1
    await store.createAgent(); // d2
    await Promise.all([store.runStream(d1.id, "q1"), store.runStream(d2.id, "q2")]);
    expect(store.agents[d1.id]?.history.map((m) => m.content)).toEqual(["1q", "1"]);
    expect(store.agents[d2.id]?.history.map((m) => m.content)).toEqual(["2q", "2"]);
    expect(store.agents[d1.id]?.streaming).toBe(false);
    expect(store.agents[d2.id]?.streaming).toBe(false);
  });
  it("switch вкладок не теряет историю: стор один, activeAgentId меняется", async () => {
    const d1 = agentDTO();
    const d2 = agentDTO();
    const handlers = new Map<string, (body?: unknown) => Response>([
      ["POST /agents", () => new Response(JSON.stringify(d1), { status: 200 })],
    ]);
    mockFetch(handlers);
    const store = useAgentsStore();
    await store.createAgent();
    handlers.set("POST /agents", () => new Response(JSON.stringify(d2), { status: 200 }));
    await store.createAgent();
    store.setActive(d1.id);
    expect(store.activeAgent?.id).toBe(d1.id);
    store.setActive(d2.id);
    expect(store.activeAgent?.history).toEqual(store.agents[d2.id]?.history);
  });
});
