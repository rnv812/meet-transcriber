/**
 * Свои параметры запуска агента во вкладке «Агент» (настройки `agent.launch`).
 *
 * Правила разбора — те же, что у оболочки (`pty.rs`: parse_launch_args,
 * with_user_args) и резидента (`meet.agent_launch`): строка параметров — в
 * отдельные аргументы без командной оболочки; переменные — строки ИМЯ=значение.
 * Здесь они нужны окну: ошибка на месте и строка «Команда запуска».
 */

export type AgentId = "claude-code" | "codex" | "opencode";
export type EnvEntry = { key: string; value: string };
/** `env` — список; строка — текст поля с ошибкой (сохранить нельзя). */
export type LaunchDraft = { args: string; env: EnvEntry[] | string };

export const AGENTS: { id: AgentId; label: string; program: string }[] = [
  { id: "claude-code", label: "Claude Code", program: "claude" },
  { id: "codex", label: "Codex", program: "codex" },
  { id: "opencode", label: "OpenCode", program: "opencode" },
];

export const ARGS_CONTROL = "Недопустимый управляющий символ в параметрах запуска";
export const ARGS_QUOTE = "Незакрытая кавычка в параметрах запуска (обратная косая черта перед кавычкой \\\" "
  + "считается частью текста — уберите её в конце пути)";

const isControl = (ch: string) => {
  const code = ch.codePointAt(0) ?? 0;
  return code < 0x20 || code === 0x7f;
};

/**
 * Строка → аргументы. Разделители — пробел и табуляция; «"…"» и «'…'»
 * объединяют текст и примыкают к соседнему; обратная косая черта — обычный
 * символ (пути Windows), только внутри двойных кавычек `\"` — сама кавычка.
 */
export function parseArgs(text: string): { args: string[]; error: null } | { args: null; error: string } {
  const args: string[] = [];
  /** Текущий аргумент и начат ли он (`""` — тоже аргумент). */
  let cur = "";
  let started = false;
  let quote: string | null = null;
  const chars = [...text];
  for (let i = 0; i < chars.length; i++) {
    const ch = chars[i] ?? "";
    if (isControl(ch) && !(ch === "\t" && quote === null)) return { args: null, error: ARGS_CONTROL };
    if (quote === null) {
      if (ch === " " || ch === "\t") {
        if (started) args.push(cur);
        cur = "";
        started = false;
      } else if (ch === '"' || ch === "'") {
        quote = ch;
        started = true;
      } else {
        cur += ch;
        started = true;
      }
    } else if (quote === "'") {
      if (ch === "'") quote = null;
      else cur += ch;
    } else if (ch === "\\" && chars[i + 1] === '"') {
      cur += '"';
      i++;
    } else if (ch === '"') {
      quote = null;
    } else {
      cur += ch;
    }
  }
  if (quote !== null) return { args: null, error: ARGS_QUOTE };
  if (started) args.push(cur);
  return { args, error: null };
}

const NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** Ошибка одной строки поля «Переменные окружения» или null. */
function envLineError(line: string, n: number, seen: Set<string>): string | null {
  const at = line.indexOf("=");
  if (at < 0) return `Строка ${n}: нужен вид ИМЯ=значение`;
  const key = line.slice(0, at).trim();
  if (!NAME.test(key)) return `Строка ${n}: недопустимое имя «${key}» (латинские буквы, цифры и _, не с цифры)`;
  if ([...line.slice(at + 1)].some(isControl)) return `Строка ${n}: управляющие символы в значении недопустимы`;
  if (seen.has(key.toUpperCase())) return `Строка ${n}: переменная ${key} уже задана`;
  seen.add(key.toUpperCase());
  return null;
}

/**
 * Поле «Переменные окружения» (строки ИМЯ=значение) → список; пустые строки
 * пропускаются. Ошибки — все, по строкам (`errors`); `error` — первая.
 */
export function parseEnv(text: string):
  { env: EnvEntry[]; error: null; errors: [] } | { env: null; error: string; errors: string[] } {
  const env: EnvEntry[] = [];
  const errors: string[] = [];
  const seen = new Set<string>();
  text.split(/\r?\n/).forEach((line, i) => {
    if (!line.trim()) return;
    const error = envLineError(line, i + 1, seen);
    if (error) errors.push(error);
    else env.push({ key: line.slice(0, line.indexOf("=")).trim(), value: line.slice(line.indexOf("=") + 1) });
  });
  const [first] = errors;
  return first === undefined ? { env, error: null, errors: [] } : { env: null, error: first, errors };
}

export const envText = (env: EnvEntry[] | string): string =>
  typeof env === "string" ? env : env.map((e) => `${e.key}=${e.value}`).join("\n");

/** Ошибки черновика одного агента (подписи у полей и запрет «Сохранить»). */
export function launchError(launch: LaunchDraft | undefined): { args: string | null; env: string[] } {
  if (!launch) return { args: null, env: [] };
  const args = parseArgs(String(launch.args ?? "")).error;
  if (typeof launch.env !== "string") return { args, env: [] };
  const { errors } = parseEnv(launch.env);
  return { args, env: errors.length ? errors : ["Исправьте переменные окружения"] };
}

/** Секрет в значении переменной — не показываем в строке «Команда запуска». */
const SECRET = /(KEY|TOKEN|SECRET|PASS|AUTH|CREDENTIAL|COOKIE)/i;
export const maskValue = (key: string, value: string) => (SECRET.test(key) && value ? "***" : value);

/** Наши аргументы (как `agent_args` оболочки) — в строке «Команда запуска». */
export const MEETING_PROMPT = "<подсказка о встрече>";
export const MEETING_FOLDER = "<папка встречи>";
/** Id сеанса Claude: новый задаёт оболочка (`--session-id`), «Продолжить» — `--resume` его же. */
export const SESSION_ID = "<id сеанса>";
/** Модель Claude Code, когда `llm.model` пусто (как `DEFAULT_CLAUDE_MODEL` резидента и оболочки). */
export const DEFAULT_CLAUDE_MODEL = "sonnet";
/**
 * `model` — `llm.model` («Модель Claude Code»): Claude получает её всегда
 * (`--model`, и в «Продолжить прошлую»), как в `with_model` оболочки.
 */
export function ourArgs(agent: AgentId, knowledge: string | null, resume = false, model: string | null = null): string[] {
  const kb = knowledge?.trim() || null;
  // OpenCode: папка встречи — рабочая папка, подсказка и база знаний — в его конфиге (окружение).
  if (agent === "opencode") return resume ? ["--continue"] : [];
  if (agent === "claude-code") {
    return [resume ? "--resume" : "--session-id", SESSION_ID, ...(kb ? ["--add-dir", kb] : []),
      "--append-system-prompt", MEETING_PROMPT, `--model=${model?.trim() || DEFAULT_CLAUDE_MODEL}`];
  }
  return [...(resume ? ["resume", "--last"] : []), "--cd", MEETING_FOLDER, "-c", `developer_instructions=${MEETING_PROMPT}`];
}

const has = (user: string[], names: string[]) =>
  user.some((a) => names.some((n) => a === n || (n.startsWith("--") && a.startsWith(`${n}=`))));

/** Свои параметры, которые сами выбирают сеанс Claude (как CLAUDE_SESSION_FLAGS оболочки). */
export const CLAUDE_SESSION_FLAGS = ["--continue", "-c", "--resume", "-r", "--session-id"];
/** Свои параметры, которые сами выбирают сеанс OpenCode (как OPENCODE_SESSION_FLAGS оболочки). */
export const OPENCODE_SESSION_FLAGS = ["--continue", "-c", "--session", "-s"];

/** Убрать флаг `name` (и его значение, если `valued`). */
function dropFlag(args: string[], name: string, valued: boolean) {
  const at = args.indexOf(name);
  if (at >= 0) args.splice(at, valued ? 2 : 1);
}

/** Наши + свои (свои последними — у повторённого параметра действует свой). Как `with_user_args`. */
export function withUserArgs(agent: AgentId, ours: string[], user: string[]): string[] {
  const args = [...ours];
  if (agent === "claude-code") {
    if (has(user, CLAUDE_SESSION_FLAGS)) {
      dropFlag(args, "--continue", false);
      dropFlag(args, "--resume", true);
      dropFlag(args, "--session-id", true);
    }
    if (has(user, ["--model"])) {
      dropFlag(args, "--model", true);
      const at = args.findIndex((a) => a.startsWith("--model="));
      if (at >= 0) args.splice(at, 1);
    }
  } else if (agent === "opencode") {
    if (has(user, OPENCODE_SESSION_FLAGS)) dropFlag(args, "--continue", false);
  } else {
    if (has(user, ["--last"])) dropFlag(args, "--last", false);
    if (has(user, ["--cd", "-C"])) dropFlag(args, "--cd", true);
  }
  return [...args, ...user];
}

const shown = (arg: string) => (arg === "" ? '""' : /[\s"']/.test(arg) ? `"${arg.replace(/"/g, '\\"')}"` : arg);

/** Переменная, которой задают модель Claude Code в «Переменных окружения» (как `MODEL_ENV` оболочки). */
export const MODEL_ENV = "ANTHROPIC_MODEL";
/** Своя ANTHROPIC_MODEL (имя без учёта регистра, пустое значение не в счёт) — как `env_model` оболочки. */
export const envModel = (env: EnvEntry[]): string | null =>
  [...env].reverse().find((e) => e.key.toUpperCase() === MODEL_ENV && e.value.trim())?.value.trim() ?? null;

/**
 * Строка «Команда запуска»: свои переменные (секреты скрыты), программа, аргументы. `model` — `llm.model`;
 * своя ANTHROPIC_MODEL в переменных — нашего `--model` нет (флаг у CLI сильнее переменной), как в оболочке.
 */
export function previewCommand(agent: AgentId, launch: LaunchDraft, knowledge: string | null,
  model: string | null = null): string | null {
  const user = parseArgs(String(launch.args ?? ""));
  if (user.error !== null || typeof launch.env === "string") return null;
  const program = AGENTS.find((a) => a.id === agent)?.program ?? agent;
  const env = launch.env.map((e) => `${e.key}=${shown(maskValue(e.key, e.value))}`);
  let ours = ourArgs(agent, knowledge, false, model);
  if (envModel(launch.env) !== null) ours = ours.filter((a) => !a.startsWith("--model="));
  return [...env, program, ...withUserArgs(agent, ours, user.args).map(shown)].join(" ");
}
