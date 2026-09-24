// Клиентский реестр slash-команд (§6).
// Список команд приходит из GET /api/commands; здесь — парсинг, валидация
// аргументов и клиентские обработчики. Команды настройки перенесены в панель
// «Настройки» (SettingsPanel), остались только close и export.
import type { CommandDTO } from "@/api/types";

export interface PaletteRequests {
  /** inline-подсказка в чат */
  note(text: string): void;
}

export interface CommandActions {
  closeAgent(): Promise<void>;
  exportSession(path?: string): Promise<string>;
}

export interface CommandContext {
  requests: PaletteRequests;
  actions: CommandActions;
}

export interface ChatCommand {
  name: string;
  description: string;
  args_spec: string;
  /** Выполняет команду; возвращает system-заметку (или null). */
  run: (ctx: CommandContext, args: string[]) => Promise<string | null> | string | null;
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

/** Собирает клиентский реестр из DTO бэкенда (§5.1, §6). */
export function buildRegistry(dtos: CommandDTO[]): ChatCommand[] {
  const byName = new Map(dtos.map((d) => [d.name, d]));
  const desc = (name: string, fallback: string): string => byName.get(name)?.description ?? fallback;
  const spec = (name: string): string => byName.get(name)?.args_spec ?? "";
  const commands: ChatCommand[] = [
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
      name: "export",
      description: desc("export", "экспорт сессии"),
      args_spec: spec("export"),
      run: async (ctx, args) => ctx.actions.exportSession(args[0]),
    },
  ];
  return commands;
}

export function findCommand(commands: ChatCommand[], cmd: string): ChatCommand | undefined {
  return commands.find((c) => c.name === cmd);
}
