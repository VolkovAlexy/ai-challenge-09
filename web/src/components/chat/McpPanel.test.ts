import { beforeEach, describe, expect, it, vi } from "vitest";
import { createPinia, setActivePinia } from "pinia";
import { mount } from "@vue/test-utils";
import McpPanel from "./McpPanel.vue";
import { useMcpStore } from "@/stores/mcp";
import type { McpDTO } from "@/api/types";

function dto(overrides: Partial<McpDTO> = {}): McpDTO {
  return {
    name: "playwright",
    transport: "stdio",
    status: "available",
    enabled: true,
    tool_count: 2,
    tools: [
      { name: "browser_navigate", description: "Открыть URL" },
      { name: "browser_snapshot", description: "Снимок страницы" },
    ],
    ...overrides,
  };
}

function seed(servers: McpDTO[]): void {
  const store = useMcpStore();
  store.servers = servers;
  store.loaded = true;
  store.loadError = null;
}

function mountPanel() {
  return mount(McpPanel, { props: { active: false } });
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.unstubAllGlobals();
});

describe("McpPanel", () => {
  it("клик по карточке раскрывает список инструментов сервера", async () => {
    seed([dto()]);
    const w = mountPanel();
    expect(w.find(".mcp-tools").exists()).toBe(false);
    await w.find(".mcp-item").trigger("click");
    expect(w.find(".mcp-tools").exists()).toBe(true);
    expect(w.find(".mcp-tool").text()).toContain("browser_navigate");
    expect(w.find(".mcp-tool").text()).toContain("Открыть URL");
    expect(w.text()).toContain("browser_snapshot");
    expect(w.text()).toContain("Снимок страницы");
  });

  it("повторный клик сворачивает инструменты обратно", async () => {
    seed([dto()]);
    const w = mountPanel();
    const item = w.find(".mcp-item");
    await item.trigger("click");
    expect(w.find(".mcp-tools").exists()).toBe(true);
    await item.trigger("click");
    expect(w.find(".mcp-tools").exists()).toBe(false);
  });

  it("сервер без инструментов показывает заглушку", async () => {
    seed([dto({ tool_count: 0, tools: [] })]);
    const w = mountPanel();
    await w.find(".mcp-item").trigger("click");
    expect(w.find(".mcp-tools-empty").text()).toBe("нет инструментов");
  });

  it("клик по свитчу не раскрывает/не сворачивает карточку", async () => {
    seed([dto()]);
    const store = useMcpStore();
    vi.spyOn(store, "toggle").mockResolvedValue();
    const w = mountPanel();
    await w.find(".mcp-switch").trigger("click");
    expect(store.toggle).toHaveBeenCalledWith("playwright", false);
    expect(w.find(".mcp-tools").exists()).toBe(false);
  });
});
