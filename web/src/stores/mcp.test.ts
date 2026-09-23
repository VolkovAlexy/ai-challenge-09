import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { useMcpStore } from "./mcp";
import type { McpDTO } from "@/api/types";

const dto = (overrides: Partial<McpDTO> = {}): McpDTO => ({
  name: "playwright",
  transport: "stdio",
  status: "available",
  enabled: true,
  tool_count: 5,
  ...overrides,
});

function mockFetch(handlers: Map<string, (body?: unknown) => Response>): void {
  vi.stubGlobal("fetch", vi.fn(async (url: string | URL, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${String(url).replace(/^\/api/, "")}`;
    const h = handlers.get(key);
    if (h === undefined) throw new Error(`нет заглушки для ${key}`);
    return h(init?.body === undefined ? undefined : JSON.parse(String(init.body)));
  }));
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.unstubAllGlobals();
});

describe("mcp store", () => {
  it("load тянет список серверов", async () => {
    const list = [dto()];
    mockFetch(new Map([["GET /mcp", () => new Response(JSON.stringify(list), { status: 200 })]]));
    const store = useMcpStore();
    await store.load();
    expect(store.loaded).toBe(true);
    expect(store.servers).toEqual(list);
    expect(store.loadError).toBeNull();
  });

  it("toggle шлёт PATCH и заменяет список ответом", async () => {
    const off = dto({ enabled: false });
    mockFetch(new Map([
      ["GET /mcp", () => new Response(JSON.stringify([dto()]), { status: 200 })],
      ["PATCH /mcp/playwright", () => new Response(JSON.stringify([off]), { status: 200 })],
    ]));
    const store = useMcpStore();
    await store.load();
    await store.toggle("playwright", false);
    expect(store.servers[0].enabled).toBe(false);
  });

  it("ошибка записывается в loadError", async () => {
    mockFetch(new Map([
      ["GET /mcp", () => new Response(JSON.stringify({ detail: "boom" }), { status: 500 })],
    ]));
    const store = useMcpStore();
    await store.load();
    expect(store.loadError).toBe("boom");
  });
});
