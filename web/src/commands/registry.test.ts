import { describe, expect, it } from "vitest";
import { buildRegistry, findCommand, parseCommand } from "./registry";
import type { CommandContext } from "./registry";
import type { CommandDTO } from "@/api/types";

const dtos: CommandDTO[] = [
  { name: "close", description: "закрыть агента", args_spec: "" },
  { name: "export", description: "экспорт сессии", args_spec: "[file]" },
];

const noopCtx: CommandContext = {
  requests: {
    note: () => {},
  },
  actions: {
    closeAgent: async () => {},
    exportSession: async () => "/x/sessions/export.jsonl",
  },
};

describe("parseCommand", () => {
  it("парсит /cmd args", () => {
    expect(parseCommand("/export x.jsonl")).toEqual({ cmd: "export", args: ["x.jsonl"] });
  });
  it("не команда: без слеша, одиночный слеш, пусто", () => {
    expect(parseCommand("привет")).toBeNull();
    expect(parseCommand("/")).toBeNull();
    expect(parseCommand("")).toBeNull();
  });
  it("неизвестная команда распознаётся как команда", () => {
    expect(parseCommand("/foobar")).toEqual({ cmd: "foobar", args: [] });
  });
});

describe("buildRegistry", () => {
  it("остались только close и export из реестра бэкенда", () => {
    const cmds = buildRegistry(dtos);
    const names = cmds.map((c) => c.name);
    for (const d of dtos) expect(names).toContain(d.name);
    expect(names).toEqual(["close", "export"]);
  });

  it("/close вызывает closeAgent", async () => {
    let closed = false;
    const ctx: CommandContext = {
      ...noopCtx,
      actions: {
        ...noopCtx.actions,
        closeAgent: async () => {
          closed = true;
        },
      },
    };
    const cmd = buildRegistry(dtos).find((c) => c.name === "close");
    expect(await cmd?.run(ctx, [])).toBeNull();
    expect(closed).toBe(true);
  });

  it("/export возвращает сообщение с путём", async () => {
    const cmd = buildRegistry(dtos).find((c) => c.name === "export");
    expect(await cmd?.run(noopCtx, [])).toBe("/x/sessions/export.jsonl");
  });

  it("неизвестная команда не в реестре", () => {
    expect(findCommand(buildRegistry(dtos), "foobar")).toBeUndefined();
  });
});
