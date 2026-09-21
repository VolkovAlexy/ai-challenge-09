// Клиентский реестр slash-команд (§6).
// Список команд приходит из GET /api/commands; здесь — парсинг, валидация
// аргументов, автодополнение и клиентские обработчики (палитры/диалоги).
import type { CommandDTO } from "@/api/types";

export interface CompletionContext {
  modelIds: () => string[];
}

export interface PaletteRequests {
  help(): void;
  model(): void;
  session(): void;
  history(): void;
  /** /system без аргумента: модалка с текущим промптом */
  systemPrompt(): void;
  /** inline-подсказка в чат */
  note(text: string): void;
}

export interface CommandActions {
  newAgent(name?: string): Promise<void>;
  closeAgent(): Promise<void>;
  rename(name: string): Promise<void>;
  setModel(model: string): Promise<void>;
  setTemperature(v: number): Promise<void>;
  setTopP(v: number): Promise<void>;
  setMaxTokens(n: number): Promise<void>;
  setStop(seqs: string[]): Promise<void>;
  setSystemPromptPath(path: string): Promise<void>;
  clearHistory(): Promise<void>;
  loadSession(sessionId: string): Promise<void>;
  exportSession(path?: string): Promise<string>;
}

export interface CommandContext {
  requests: PaletteRequests;
  actions: CommandActions;
  modelIds: () => string[];
  currentModel: () => string | null;
}

export interface ChatCommand {
  name: string;
  description: string;
  args_spec: string;
  /** Выполняет команду; возвращает system-заметку (или null). */
  run: (ctx: CommandContext, args: string[]) => Promise<string | null> | string | null;
  /** Дополнение аргумента по префиксу (для Tab). */
  complete?: (ctx: CompletionContext, prefix: string) => string[];
}

function parseLine(line: string): { cmd: string; args: string[] } | null {
  const text = line.trim();
  if (!text.startsWith("/") || text.length < 2) return null;
  const parts = text.slice(1).split(/\s+/);
  return { cmd: parts[0].toLowerCase(), args: parts.slice(1) };
}

/** Разбор строки ввода: команда или нет (публично для ChatInput/useChatStream). */
export function parseCommand(line: string): { cmd: string; args: string[] } | null {
  return parseLine(line);
}

function num(args: string[], min: number, max: number): number | null {
  if (args.length !== 1) return null;
  const v = Number(args[0]);
  if (Number.isNaN(v) || v < min || v > max) return null;
  return v;
}

/** Собирает клиентский реестр из DTO бэкенда (§5.1, §6). */
export function buildRegistry(dtos: CommandDTO[]): ChatCommand[] {
  const byName = new Map(dtos.map((d) => [d.name, d]));
  const desc = (name: string, fallback: string): string => byName.get(name)?.description ?? fallback;
  const spec = (name: string): string => byName.get(name)?.args_spec ?? "";
  const commands: ChatCommand[] = [
    {
      name: "help",
      description: desc("help", "список команд"),
      args_spec: spec("help"),
      run: (ctx) => {
        ctx.requests.help();
        return null;
      },
    },
    {
      name: "new",
      description: desc("new", "новый агент"),
      args_spec: spec("new"),
      run: async (ctx, args) => {
        await ctx.actions.newAgent(args[0]);
        return null;
      },
    },
    {
      name: "close",
      description: desc("close", "закрыть агента"),
      args_spec: spec("close"),
      run: async (ctx) => {
        await ctx.actions.closeAgent();
        return null;
      },
    },
    {
      name: "name",
      description: desc("name", "переименовать агента"),
      args_spec: spec("name"),
      run: async (ctx, args) => {
        if (args.length === 0) return "Использование: /name <имя>";
        await ctx.actions.rename(args.join(" "));
        return null;
      },
    },
    {
      name: "model",
      description: desc("model", "выбор модели"),
      args_spec: spec("model"),
      complete: (ctx, prefix) => ctx.modelIds().filter((m) => m.startsWith(prefix)),
      run: async (ctx, args) => {
        if (args.length === 0) {
          ctx.requests.model();
          return null;
        }
        await ctx.actions.setModel(args.join(":"));
        return null;
      },
    },
    {
      name: "temperature",
      description: desc("temperature", "temperature 0..2"),
      args_spec: spec("temperature"),
      run: async (ctx, args) => {
        const v = num(args, 0, 2);
        if (v === null) return "temperature — число от 0 до 2";
        await ctx.actions.setTemperature(v);
        return null;
      },
    },
    {
      name: "top-p",
      description: desc("top-p", "top_p 0..1"),
      args_spec: spec("top-p"),
      run: async (ctx, args) => {
        const v = num(args, 0, 1);
        if (v === null) return "top-p — число от 0 до 1";
        await ctx.actions.setTopP(v);
        return null;
      },
    },
    {
      name: "max-tokens",
      description: desc("max-tokens", "максимум токенов"),
      args_spec: spec("max-tokens"),
      run: async (ctx, args) => {
        const v = num(args, 1, 1_000_000);
        if (v === null) return "max-tokens — целое число > 0";
        await ctx.actions.setMaxTokens(v);
        return null;
      },
    },
    {
      name: "stop",
      description: desc("stop", "stop-sequences через запятую"),
      args_spec: spec("stop"),
      run: async (ctx, args) => {
        if (args.length === 0) return "Использование: /stop <seq,...>";
        await ctx.actions.setStop(args.join(" ").split(",").map((s) => s.trim()).filter((s) => s !== ""));
        return null;
      },
    },
    {
      name: "system",
      description: desc("system", "системный промпт"),
      args_spec: spec("system"),
      run: async (ctx, args) => {
        if (args.length === 0) {
          ctx.requests.systemPrompt();
          return null;
        }
        await ctx.actions.setSystemPromptPath(args[0]);
        return null;
      },
    },
    {
      name: "history",
      description: desc("history", "история диалога"),
      args_spec: spec("history"),
      run: (ctx) => {
        ctx.requests.history();
        return null;
      },
    },
    {
      name: "clear",
      description: desc("clear", "очистить историю"),
      args_spec: spec("clear"),
      run: async (ctx) => {
        await ctx.actions.clearHistory();
        return "История очищена.";
      },
    },
    {
      name: "session",
      description: desc("session", "загрузить сессию"),
      args_spec: spec("session"),
      run: (ctx) => {
        ctx.requests.session();
        return null;
      },
    },
    {
      name: "export",
      description: desc("export", "экспорт сессии"),
      args_spec: spec("export"),
      run: async (ctx, args) => ctx.actions.exportSession(args[0]),
    },
    {
      name: "exit",
      description: desc("exit", "закрыть вкладку"),
      args_spec: spec("exit"),
      run: async (ctx) => {
        await ctx.actions.closeAgent();
        return null;
      },
    },
  ];
  return commands;
}

/** Автодополнение: по префиксу `/<pa` — имена команд; для `/model` — модели. */
export function completeInput(commands: ChatCommand[], ctx: CompletionContext, line: string): string[] {
  // line не trim-им целиком: хвостовой пробел обязан давать пустую часть (parts.length>1),
  // иначе "/help " снова дополнится в "/help" и меню зациклится.
  const parts = line.slice(1).trimStart().split(/\s+/);
  if (parts.length === 1 && parts[0] !== "") {
    const prefix = parts[0].toLowerCase();
    return commands.filter((c) => c.name.startsWith(prefix)).map((c) => `/${c.name}`);
  }
  const cmd = commands.find((c) => c.name === parts[0].toLowerCase());
  if (cmd?.complete === undefined) return [];
  return cmd.complete(ctx, parts.slice(1).join(" ")).map((s) => `/${cmd.name} ${s}`);
}

export function findCommand(commands: ChatCommand[], cmd: string): ChatCommand | undefined {
  return commands.find((c) => c.name === cmd);
}
