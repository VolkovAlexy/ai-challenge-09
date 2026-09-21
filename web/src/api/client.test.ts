import { afterEach, describe, expect, it, vi } from "vitest";
import { api, ApiError } from "./client";

function jsonOk(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("api client", () => {
  it("GET /config возвращает DTO", async () => {
    const dto = { providers: {}, default_model: "p:m" };
    vi.stubGlobal("fetch", vi.fn(async () => jsonOk(dto)));
    expect(await api.getConfig()).toEqual(dto);
    const call = (fetch as ReturnType<typeof vi.fn>).mock.calls[0] as unknown[];
    expect(call[0]).toBe("/api/config");
  });

  it("ошибка бэкенда маппится в ApiError с detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"detail":"модель не найдена"}', { status: 400 })));
    const err = await api.getConfig().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(400);
    expect((err as ApiError).message).toBe("модель не найдена");
  });

  it("сетевой сбой — ApiError(0)", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => {
      throw new TypeError("failed");
    }));
    const err = await api.listAgents().catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(0);
  });

  it("createAgent с именем шлёт {name}, без — {}", async () => {
    const fetchMock = vi.fn(async () => jsonOk({ id: "a1" }));
    vi.stubGlobal("fetch", fetchMock);
    await api.createAgent("my-chat");
    await api.createAgent();
    const bodies = (fetchMock.mock.calls as unknown[][]).map((c) => c[1] as RequestInit);
    expect(bodies[0]?.body).toBe(JSON.stringify({ name: "my-chat" }));
    expect(bodies[1]?.body).toBe("{}");
  });

  it("409 при /close во время стрима пробрасывается с detail", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"detail":"агент отвечает"}', { status: 409 })));
    const err = await api.closeAgent("a1").catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe("агент отвечает");
  });

  it("sendMessage парсит SSE-события в StreamEvent", async () => {
    const body =
      'data: {"event":"user_message","message":{"id":"1","role":"user","content":"привет"}}\n\n' +
      'data: {"event":"delta","content":"Добр"}\n\n' +
      'data: {"event":"done","message":{"id":"2","role":"assistant","content":"Добрый"}}\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
    const out = [];
    for await (const ev of api.sendMessage("a1", "привет")) out.push(ev);
    expect(out.map((e) => e.event)).toEqual(["user_message", "delta", "done"]);
  });

  it("sendMessage при не-OK ответе бросает ApiError до чтения потока", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response('{"detail":"нет такого агента"}', { status: 404 })));
    const it = api.sendMessage("x", "hi")[Symbol.asyncIterator]();
    const err = await it.next().catch((e: unknown) => e);
    expect((err as ApiError).status).toBe(404);
  });

  it("битый JSON внутри SSE-события превращается в error-событие, поток не падает", async () => {
    const body = 'data: не-json\n\ndata: {"event":"done","message":{"id":"2","role":"assistant","content":""}}\n\n';
    vi.stubGlobal("fetch", vi.fn(async () => new Response(body, { status: 200 })));
    const out = [];
    for await (const ev of api.sendMessage("a1", "hi")) out.push(ev);
    expect(out[0].event).toBe("error");
    expect(out[1].event).toBe("done");
  });
});
