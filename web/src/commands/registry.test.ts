import { describe, expect, it } from "vitest";
import { buildRegistry, completeInput, findCommand, parseCommand } from "./registry";
import type { CommandContext } from "./registry";
import type { CommandDTO } from "@/api/types";

const dtos: CommandDTO[] = [
  { name: "help", description: "список команд", args_spec: "" },
  { name: "new", description: "новый агент [name]", args_spec: "[name]" },
  { name: "close", description: "закрыть агента", args_spec: "" },
  { name: "name", description: "переименовать агента", args_spec: "<name>" },
  { name: "model", description: "палитра выбора модели", args_spec: "[provider:model]" },
  { name: "temperature", description: "temperature", args_spec: "<0..2>" },
  { name: "top-p", description: "top_p", args_spec: "<0..1>" },
  { name: "max-tokens", description: "максимум токенов", args_spec: "<n>" },
  { name: "stop", description: "stop-sequences", args_spec: "<seq,...>" },
  { name: "system", description: "системный промпт", args_spec: "[path]" },
  { name: "history", description: "история диалога", args_spec: "" },
  { name: "clear", description: "очистить историю", args_spec: "" },
  { name: "session", description: "сессии", args_spec: "" },
  { name: "export", description: "экспорт сессии", args_spec: "[file]" },
  { name: "exit", description: "выход", args_spec: "" },
];

const noopCtx: CommandContext = {
  requests: {
    help: () => {},
    model: () => {},
    session: () => {},
    history: () => {},
    systemPrompt: () => {},
    note: () => {},
  },
  actions: {
    newAgent: async () => {},
    closeAgent: async () => {},
    rename: async () => {},
    setModel: async () => {},
    setTemperature: async () => {},
    setTopP: async () => {},
    setMaxTokens: async () => {},
    setStop: async () => {},
    setSystemPromptPath: async () => {},
    clearHistory: async () => {},
    loadSession: async () => {},
    exportSession: async () => "/x/sessions/export.jsonl",
  },
  modelIds: () => ["ollama:llama3.1", "openai:gpt-4"],
  currentModel: () => "ollama:llama3.1",
};

describe("parseCommand", () => {
  it("парсит /cmd args", () => {
    expect(parseCommand("/temperature 0.5")).toEqual({ cmd: "temperature", args: ["0.5"] });
    expect(parseCommand("/name  Мой чат ")).toEqual({ cmd: "name", args: ["Мой", "чат"] });
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
  it("все команды v1 из реестра бэкенда присутствуют", () => {
    const cmds = buildRegistry(dtos);
    const names = cmds.map((c) => c.name);
    for (const d of dtos) expect(names).toContain(d.name);
  });

  it("валидация /temperature: 0..2", async () => {
    const t = buildRegistry(dtos).find((c) => c.name === "temperature");
    expect(await t?.run(noopCtx, ["1.5"])).toBeNull();
    expect(await t?.run(noopCtx, ["2.5"])).toContain("0 до 2");
    expect(await t?.run(noopCtx, [])).toContain("0 до 2");
  });

  it("валидация /top-p: 0..1", async () => {
    const t = buildRegistry(dtos).find((c) => c.name === "top-p");
    expect(await t?.run(noopCtx, ["1"])).toBeNull();
    expect(await t?.run(noopCtx, ["1.5"])).toContain("0 до 1");
  });

  it("валидация /max-tokens: целое > 0", async () => {
    const t = buildRegistry(dtos).find((c) => c.name === "max-tokens");
    expect(await t?.run(noopCtx, ["4096"])).toBeNull();
    expect(await t?.run(noopCtx, ["-5"])).toContain("целое");
  });

  it("/model с аргументом вызывает setModel, без — палитру", async () => {
    let palette = false;
    let setModel: string | null = null;
    const ctx: CommandContext = {
      ...noopCtx,
      requests: { ...noopCtx.requests, model: () => (palette = true) },
      actions: {
        ...noopCtx.actions,
        setModel: async (m: string) => {
          setModel = m;
        },
      },
    };
    const cmds = buildRegistry(dtos);
    const model = cmds.find((c) => c.name === "model");
    await model?.run(ctx, []);
    expect(palette).toBe(true);
    await model?.run(ctx, ["openai", "gpt-4"]);
    expect(setModel).toBe("openai:gpt-4");
  });

  it("неизвестная команда не в реестре", () => {
    expect(findCommand(buildRegistry(dtos), "foobar")).toBeUndefined();
  });
});

describe("completeInput", () => {
  const cmds = buildRegistry(dtos);
  it("по префиксу команды", () => {
    expect(completeInput(cmds, noopCtx, "/te")).toEqual(["/temperature"]);
    expect(completeInput(cmds, noopCtx, "/e")).toEqual(["/export", "/exit"]);
  });
  it("по префиксу модели для /model", () => {
    expect(completeInput(cmds, noopCtx, "/model ollama")).toEqual(["/model ollama:llama3.1"]);
  });
  it("без слеша — нет вариантов", () => {
    expect(completeInput(cmds, noopCtx, "привет")).toEqual([]);
  });
  it("хвостовой пробел закрывает меню имени (не зацикливает дополнение)", () => {
    expect(completeInput(cmds, noopCtx, "/help ")).toEqual([]);
  });
});
