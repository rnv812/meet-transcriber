/**
 * Свои параметры запуска агента во вкладке «Агент» (настройки `agent.launch`).
 *
 * Правила разбора — те же, что у оболочки (`pty.rs`: parse_launch_args,
 * with_user_args) и резидента (`meet.agent_launch`): строка параметров — в
 * отдельные аргументы без командной оболочки; переменные — строки ИМЯ=значение.
 * Здесь они нужны окну: ошибка на месте и строка «Команда запуска».
 */

export type AgentId = "claude-code" | "codex";
export type EnvEntry = { key: string; value: string };
/** `env` — список; строка — текст поля с ошибкой (сохранить нельзя). */
export type LaunchDraft = { args: string; env: EnvEntry[] | string };

export const AGENTS: { id: AgentId; label: string; program: string }[] = [
  { id: "claude-code", label: "Claude Code", program: "claude" },
  { id: "codex", label: "Codex", program: "codex" },
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

/** Поле «Переменные окружения» (строки ИМЯ=значение) → список или ошибка. */
export function parseEnv(text: string): { env: EnvEntry[]; error: null } | { env: null; error: string } {
  const env: EnvEntry[] = [];
  const seen = new Set<string>();
  const lines = text.split(/\r?\n/);
  for (let n = 0; n < lines.length; n++) {
    const line = lines[n] ?? "";
    if (!line.trim()) continue;
    const at = line.indexOf("=");
    if (at < 0) return { env: null, error: `Строка ${n + 1}: нужен вид ИМЯ=значение` };
    const key = line.slice(0, at).trim();
    const value = line.slice(at + 1);
    if (!NAME.test(key)) {
      return { env: null, error: `Строка ${n + 1}: недопустимое имя «${key}» (латинские буквы, цифры и _, не с цифры)` };
    }
    if ([...value].some(isControl)) return { env: null, error: `Строка ${n + 1}: управляющие символы в значении недопустимы` };
    if (seen.has(key.toUpperCase())) return { env: null, error: `Строка ${n + 1}: переменная ${key} уже задана` };
    seen.add(key.toUpperCase());
    env.push({ key, value });
  }
  return { env, error: null };
}

export const envText = (env: EnvEntry[] | string): string =>
  typeof env === "string" ? env : env.map((e) => `${e.key}=${e.value}`).join("\n");

/** Ошибка черновика одного агента (для подписи у поля и запрета «Сохранить»). */
export function launchError(launch: LaunchDraft | undefined): { args: string | null; env: string | null } {
  if (!launch) return { args: null, env: null };
  const args = parseArgs(String(launch.args ?? "")).error;
  const env = typeof launch.env === "string" ? parseEnv(launch.env).error ?? "Исправьте переменные окружения" : null;
  return { args, env };
}

/** Секрет в значении переменной — не показываем в строке «Команда запуска». */
const SECRET = /(KEY|TOKEN|SECRET|PASS|AUTH|CREDENTIAL|COOKIE)/i;
export const maskValue = (key: string, value: string) => (SECRET.test(key) && value ? "***" : value);

/** Наши аргументы (как `agent_args` оболочки) — в строке «Команда запуска». */
export const MEETING_PROMPT = "<подсказка о встрече>";
export const MEETING_FOLDER = "<папка встречи>";
export function ourArgs(agent: AgentId, knowledge: string | null, resume = false): string[] {
  const kb = knowledge?.trim() || null;
  if (agent === "claude-code") {
    return [...(resume ? ["--continue"] : []), ...(kb ? ["--add-dir", kb] : []), "--append-system-prompt", MEETING_PROMPT];
  }
  return [...(resume ? ["resume", "--last"] : []), "--cd", MEETING_FOLDER, "-c", `developer_instructions=${MEETING_PROMPT}`];
}

const has = (user: string[], names: string[]) =>
  user.some((a) => names.some((n) => a === n || (n.startsWith("--") && a.startsWith(`${n}=`))));

/** Наши + свои (свои последними — у повторённого параметра действует свой). Как `with_user_args`. */
export function withUserArgs(agent: AgentId, ours: string[], user: string[]): string[] {
  let args = [...ours];
  if (agent === "claude-code") {
    if (has(user, ["--continue", "-c", "--resume", "-r"])) args = args.filter((a) => a !== "--continue");
  } else {
    if (has(user, ["--last"])) args = args.filter((a) => a !== "--last");
    if (has(user, ["--cd", "-C"])) {
      const at = args.indexOf("--cd");
      if (at >= 0) args.splice(at, 2);
    }
  }
  return [...args, ...user];
}

const shown = (arg: string) => (arg === "" ? '""' : /[\s"']/.test(arg) ? `"${arg.replace(/"/g, '\\"')}"` : arg);

/** Строка «Команда запуска»: свои переменные (секреты скрыты), программа, аргументы. */
export function previewCommand(agent: AgentId, launch: LaunchDraft, knowledge: string | null): string | null {
  const user = parseArgs(String(launch.args ?? ""));
  if (user.error !== null || typeof launch.env === "string") return null;
  const program = AGENTS.find((a) => a.id === agent)?.program ?? agent;
  const env = launch.env.map((e) => `${e.key}=${shown(maskValue(e.key, e.value))}`);
  return [...env, program, ...withUserArgs(agent, ourArgs(agent, knowledge), user.args).map(shown)].join(" ");
}
