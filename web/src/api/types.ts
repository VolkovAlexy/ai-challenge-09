// DTO и события стрима — зеркало контракта бэкенда (WEB_UI.MD §5).
// Ключи API никогда не приходят на фронтенд — в этих типах их нет.

export type Role = "user" | "assistant" | "system" | "tool";

export interface UsageDTO {
  prompt_tokens?: number;
  completion_tokens?: number;
  reasoning_tokens?: number;
  /** оценка chars/4, а не точные данные API — показывается с «~» */
  approx?: boolean;
}

export interface MessageDTO {
  id: string;
  role: Role;
  content: string;
  reasoning?: string;
  /** имя инструмента (роль tool) */
  tool_name?: string;
  /** имена инструментов, вызванных ассистентом в этом сообщении */
  tool_calls?: string[];
  /** null у user/tool-сообщений — usage есть только у assistant */
  usage?: UsageDTO | null;
  error?: { kind: string; detail: string };
}



export interface AgentSettingsDTO {
  temperature: number;
  top_p: number;
  max_tokens: number;
  stop: string[];
}

export type TaskPhase = "idle" | "planning" | "execution" | "validation" | "done";

/** Состояние задачи — конечный автомат (этап, шаг, ожидаемое действие). */
export interface TaskStateDTO {
  phase: TaskPhase;
  step: number;
  /** шаги ВЫПОЛНЕНИЯ плана */
  steps: string[];
  /** шаги ПРОВЕРКИ плана */
  validation_steps: string[];
  expected_action: string;
  description: string;
  paused: boolean;
  /* план подтверждён пользователем — только тогда можно перейти в «выполнение» */
  plan_confirmed: boolean;
}

export interface AgentDTO {
  id: string;
  name: string;
  model: string; // provider:model
  settings: AgentSettingsDTO;
  system_prompt: { path: string; content: string };
  /** проект, которому принадлежит вкладка (Слой 1) */
  project_id?: string;
  context_used: number;
  context_window: number;
  streaming: boolean;
  compacting: boolean;
  /** рабочая память текущей задачи (scratchpad) */
  scratchpad: string;
  /** предложение агента сохранить знание в долговременную память */
  memory_suggestion: string | null;
  /** активный профиль роли чата ("" — без него) */
  active_profile_id?: string;
  /** состояние задачи (конечный автомат); null/undefined — задача не задана */
  task?: TaskStateDTO | null;
  /** ограничения (инварианты) сессии */
  invariants?: string[];
}

export interface ProviderDTO {
  api_base: string;
  models: Record<string, { context_window?: number }>;
}

export interface ConfigDTO {
  providers: Record<string, ProviderDTO>;
  default_model: string;
  temperature: number;
  top_p: number;
  max_tokens: number;
  stop: string[];
  context_window_default: number;
  compaction_threshold: number;
  sliding_window: number;
}

export interface CommandDTO {
  name: string;
  description: string;
  args_spec: string;
}

export interface SessionInfoDTO {
  id: string;
  title: string;
  updated_at: string;
  model?: string;
  message_count?: number;
  /** проект, которому принадлежит сессия */
  project_id?: string;
}

/** Проект (Слой 1): владеет долгосрочной памятью и набором сессий. */
export interface ProjectDTO {
  id: string;
  name: string;
  session_count: number;
  updated_at: string;
  /** профили, привязанные к проекту */
  profile_ids?: string[];
}

/** Глобальный профиль роли: имя + текст (обогащает/переопределяет базовый промпт). */
export interface ProfileDTO {
  id: string;
  name: string;
  content: string;
}

export type McpStatus = "connecting" | "available" | "unavailable";

/** Описание одного инструмента MCP-сервера. */
export interface McpToolDTO {
  name: string;
  description: string;
}

/** Состояние MCP-сервера для панели (команда/URL не отдаются). */
export interface McpDTO {
  name: string;
  transport: "stdio" | "http";
  status: McpStatus;
  enabled: boolean;
  tool_count: number;
  tools?: McpToolDTO[];
}

/** Тело PATCH /api/mcp/{name} — глобальный вкл/выкл сервера. */
export interface McpPatchRequest {
  enabled: boolean;
}

export interface PatchAgentDTO {
  name?: string;
  model?: string;
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  stop?: string[];
  system_prompt_path?: string;
  active_profile_id?: string;
}

export type TaskCommandOperation =
  | "start"
  | "set_phase"
  | "advance"
  | "pause"
  | "resume"
  | "reset"
  | "set_expected_action"
  | "confirm_plan";

/** Тело POST /api/agents/{id}/task — команда управления конечным автоматом. */
export interface TaskCommandRequest {
  operation: TaskCommandOperation;
  description?: string;
  steps?: string[];
  validation_steps?: string[];
  /* advance: отметить текущий шаг выполненным и перейти дальше */
  done?: boolean;
  phase?: TaskPhase;
  expected_action?: string;
}

/** События SSE-потока ответа (§5.2). Терминалы: done / cancelled / error. */
export type StreamEvent =
  | { event: "user_message"; message: MessageDTO }
  | { event: "compaction_started" }
  | { event: "compaction_done"; removed: number; summary_tokens: number; pct_before: number; pct_after: number }
  | { event: "delta"; content: string }
  | { event: "reasoning_delta"; content: string }
  | { event: "tool_message"; message: MessageDTO }
  | { event: "subagent_started"; profile: string }
  | { event: "subagent_delta"; profile: string; content: string }
  | { event: "subagent_done"; profile: string }
  | { event: "scratchpad"; content: string }
  | { event: "task"; task: TaskStateDTO | null }
  | { event: "invariants"; invariants: string[] }
  | { event: "done"; message: MessageDTO }
  | { event: "cancelled" }
  | { event: "memory_suggestion"; content: string }
  | { event: "error"; kind: "http" | "network"; detail: string };

export interface LongTermDTO {
  /** пустой для SQL-хранилища (записи в longterm_entries) */
  path?: string;
  /** проект, чья память запрошена */
  project_id?: string;
  content: string;
  entries: string[];
}

/** Ответ fork-эндпоинта: новый агент + история его ветки. */
export interface ForkResponseDTO {
  agent: AgentDTO;
  messages: MessageDTO[];
}
