import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useChatStream } from "./useChatStream";
import { useAgentsStore } from "@/stores/agents";
import type { AgentDTO } from "@/api/types";

function agentDTO(): AgentDTO {
  return {
    id: "a1",
    name: "chat-1",
    model: "ollama:m1",
    settings: { temperature: 0.7, top_p: 1, max_tokens: 100, stop: [] },
    system_prompt: { path: "SYSTEM_PROMPT.md", content: "ты" },
    context_used: 5,
    context_window: 100,
    streaming: false,
    compacting: false,
    scratchpad: "",
    memory_suggestion: null,
  };
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

async function seedAgent(): Promise<ReturnType<typeof useAgentsStore>> {
  const store = useAgentsStore();
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => new Response(JSON.stringify(agentDTO()), { status: 200 })),
  );
  await store.createAgent();
  return store;
}

describe("useChatStream", () => {
  it("N дельт -> 1 обновление за 100 мс (батчинг)", async () => {
    const store = await seedAgent();
    const deltas = Array.from({ length: 10 }, (_, i) => ({ event: "delta" as const, content: `ч${i} ` }));
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          deltas.map((d) => `data: ${JSON.stringify(d)}\n\n`).join(""),
          { status: 200 },
        ),
      ),
    );
    const stream = useChatStream(() => "a1");
    const done = stream.send("вопрос");
    await vi.advanceTimersByTimeAsync(0); // запустить итерацию до дельт
    await vi.advanceTimersByTimeAsync(1);
    await vi.advanceTimersByTimeAsync(100); // один тик таймера
    const midHistory = store.agents["a1"]?.history.filter((m) => m.role === "assistant");
    expect(midHistory.length).toBe(1);
    await vi.advanceTimersByTimeAsync(1000);
    await done;
    const state = store.agents["a1"];
    const full = state?.history.find((m) => m.role === "assistant");
    expect(full?.content).toContain("ч9");
    expect(state?.streaming).toBe(false);
  });

  it("финальный flush после done: буфер не теряется", async () => {
    const store = await seedAgent();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response('data: {"event":"delta","content":"Ответ"}\n\n', { status: 200 }),
      ),
    );
    const stream = useChatStream(() => "a1");
    const done = stream.send("в");
    await vi.advanceTimersByTimeAsync(1);
    await done; // flush без ожидания таймера
    const content = store.agents["a1"]?.history.find((m) => m.role === "assistant")?.content;
    expect(content).toBe("Ответ");
  });

  it("done заменяет накопленное финальным сообщением с usage", async () => {
    const store = await seedAgent();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          'data: {"event":"delta","content":"частич"}\n\n' +
            'data: {"event":"done","message":{"id":"m2","role":"assistant","content":"частичный ответ","usage":{"prompt_tokens":9,"completion_tokens":4}}}\n\n',
          { status: 200 },
        ),
      ),
    );
    const stream = useChatStream(() => "a1");
    const done = stream.send("в");
    await vi.advanceTimersByTimeAsync(1);
    await done;
    const state = store.agents["a1"];
    expect(state?.history.filter((m) => m.role === "assistant")).toHaveLength(1);
    expect(state?.history[0]?.id).toBe("m2");
    expect(state?.tokensIn).toBe(9);
  });

  it("отмена: cancel вызывает POST cancel, частичный текст остаётся", async () => {
    const store = await seedAgent();
    // бесконечный поток: дельты без терминала — читаем через ReadableStream, держим открытым
    let controller: ReadableStreamDefaultController<Uint8Array> | null = null;
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          new ReadableStream({
            start(c) {
              controller = c;
              c.enqueue(new TextEncoder().encode('data: {"event":"user_message","message":{"id":"u","role":"user","content":"в"}}\n\n'));
              c.enqueue(new TextEncoder().encode('data: {"event":"delta","content":"Част"}\n\n'));
            },
          }),
          { status: 200 },
        ),
      ),
    );
    const stream = useChatStream(() => "a1");
    const done = stream.send("в");
    await vi.advanceTimersByTimeAsync(1);
    const cancelFetch = vi.fn(async () => new Response("{}", { status: 200 }));
    const realFetch = globalThis.fetch;
    globalThis.fetch = cancelFetch as typeof fetch;
    await stream.cancel();
    globalThis.fetch = realFetch;
    (controller as ReadableStreamDefaultController<Uint8Array> | null)?.close();
    await done;
    const state = store.agents["a1"];
    expect(state?.cancelled).toBe(true);
    expect(state?.streaming).toBe(false);
  });

  it("ошибка сети пишется в историю как system с error", async () => {
    const store = await seedAgent();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        throw new TypeError("failed");
      }),
    );
    const stream = useChatStream(() => "a1");
    await stream.send("в");
    const last = store.agents["a1"]?.history.at(-1);
    expect(last?.error?.kind).toBe("network");
    expect(store.agents["a1"]?.streaming).toBe(false);
  });

  it("memory_suggestion показывает баннер предложения памяти", async () => {
    const store = await seedAgent();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          'data: {"event":"delta","content":"ок"}\n\n' +
            'data: {"event":"memory_suggestion","content":"Пользователь любит кофе"}\n\n' +
            'data: {"event":"done","message":{"id":"m1","role":"assistant","content":"ок"}}\n\n',
          { status: 200 },
        ),
      ),
    );
    const stream = useChatStream(() => "a1");
    const done = stream.send("в");
    await vi.advanceTimersByTimeAsync(1);
    await done;
    expect(store.agents["a1"]?.memorySuggestion).toBe("Пользователь любит кофе");
  });

  it("tool_message и scratchpad попадают в состояние агента", async () => {
    const store = await seedAgent();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          'data: {"event":"user_message","message":{"id":"m0","role":"user","content":"в"}}\n\n' +
            'data: {"event":"delta","content":"сейчас запишу"}\n\n' +
            'data: {"event":"tool_message","message":{"id":"m1","role":"assistant","content":"сейчас запишу","tool_calls":["write_scratchpad"]}}\n\n' +
            'data: {"event":"tool_message","message":{"id":"m2","role":"tool","content":"записано","tool_name":"write_scratchpad"}}\n\n' +
            'data: {"event":"scratchpad","content":"план: 1) тесты"}\n\n' +
            'data: {"event":"delta","content":"готово"}\n\n' +
            'data: {"event":"done","message":{"id":"m3","role":"assistant","content":"готово"}}\n\n',
          { status: 200 },
        ),
      ),
    );
    const stream = useChatStream(() => "a1");
    const done = stream.send("в");
    await vi.advanceTimersByTimeAsync(1);
    await done;
    const state = store.agents["a1"];
    expect(state?.scratchpad).toBe("план: 1) тесты");
    // порядок истории совпадает с бэкендом: user, assistant(tcs), tool, assistant
    expect(state?.history.map((m) => m.role)).toEqual(["user", "assistant", "tool", "assistant"]);
    expect(state?.history[1]?.tool_calls).toEqual(["write_scratchpad"]);
    expect(state?.history[2]?.tool_name).toBe("write_scratchpad");
    expect(state?.history[3]?.content).toBe("готово");
  });

  it("tool_message с usage:null не роняет ход", async () => {
    const store = await seedAgent();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        new Response(
          'data: {"event":"user_message","message":{"id":"m0","role":"user","content":"в"}}\n\n' +
            'data: {"event":"tool_message","message":{"id":"m1","role":"tool","content":"шаги","tool_name":"read_scratchpad","usage":null}}\n\n' +
            'data: {"event":"done","message":{"id":"m2","role":"assistant","content":"в рабочей памяти: шаги","usage":null}}\n\n',
          { status: 200 },
        ),
      ),
    );
    const stream = useChatStream(() => "a1");
    const done = stream.send("что в рабочей памяти?");
    await vi.advanceTimersByTimeAsync(1);
    await done;
    const state = store.agents["a1"];
    expect(state?.history.map((m) => m.role)).toEqual(["user", "tool", "assistant"]);
    expect(state?.tokensIn).toBe(0); // null-usage не добавляется к счётчикам
    expect(state?.tokensOut).toBe(0);
    expect(state?.streaming).toBe(false);
  });
});
