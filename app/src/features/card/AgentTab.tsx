/**
 * Вкладка «Агент»: настоящий Claude Code или Codex во встроенном терминале,
 * запущенный в папке встречи (там transcript.md и summary.md).
 *
 * Терминал — xterm.js; процесс агента живёт в оболочке (Rust, псевдоконсоль):
 * ввод уходит командой `agent_write`, вывод приходит событиями `agent-data`.
 * Вкладка остаётся смонтированной при переключении (CardTabs), поэтому сеанс
 * переживает переход на «Итоги» и обратно; закрытие карточки (другая запись,
 * другой раздел) агента останавливает.
 *
 * «Спросить агента» (✦ у реплики, пункта итогов, подсказки ассистента)
 * приходит пропом `insert`: вкладка запускает агента, если он не запущен, и
 * вставляет ссылку в поле ввода так же, как вставку из буфера (`term.paste`),
 * всегда одной строкой (`pasteLine`: ни \r, ни \n, ни ESC — Enter вставка не
 * нажмёт). Вставка ждёт, пока агент готов: включил режим bracketed paste,
 * вывод затих, на экране не вопрос первого запуска («доверять ли папке» —
 * тогда подсказка «Подтвердите запуск агента…»). Признаков готовности нет —
 * кнопка «Вставить реплику». Агента нет, не запустился или закрылся до
 * вставки — ссылку показывает уведомление, её можно скопировать.
 */

import { useCallback, useEffect, useRef, useState, type MouseEvent } from "react";
import type { ITerminalOptions, Terminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import "@xterm/xterm/css/xterm.css";
import { pasteLine } from "../../lib/agentRef";
import { getAgentContext, type Endpoint } from "../../lib/api";
import { errorText } from "../../lib/format";
import {
  agentKill, agentResize, agentSpawn, agentWrite, inTauri, onAgentData, onAgentExit,
} from "../../lib/shell";
import type { AssistantInfo } from "../../lib/types";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { PastQuestions } from "./PastQuestions";
import "./agent.css";

/** Ссылка для поля ввода агента (lib/agentRef). Новый объект — новая просьба. */
export type AgentInsert = { text: string };

/** Сколько ждать признаков готовности агента; дальше — кнопка «Вставить реплику». */
export const PASTE_WAIT_MS = 15_000;
/** Вывод агента затих на столько — экран дорисован, можно вставлять. */
export const QUIET_MS = 800;
const POLL_MS = 100;

/**
 * Экран, куда вставлять нельзя: вопрос первого запуска или подтверждение.
 * Тексты — из самих программ (строки в claude.exe и codex.exe, 2026-10):
 * Claude Code — «Accessing workspace: … Quick safety check: Is this a project
 * you created or one you trust?», «Yes, I trust this folder», «No, exit»,
 * выбор темы и входа; прежние версии — «Do you trust the files in this
 * folder?», «Yes, proceed». Codex — «Trust this folder? … Continue only if you
 * trust these files», «Press Enter to continue», «Yes, continue anyway»,
 * «Allow Codex to work in this folder…». Общие — «(y/n)».
 */
export const CONFIRM_SCREEN = new RegExp([
  "quick safety check", "accessing workspace", "one you trust", "if you trust", "yes, (i trust|continue)",
  "do you trust", "trust (the|this|these) (files|folder|directory|workspace)", "yes,? proceed", "no,? exit",
  "allow codex", "require approval", "approval (of|for|policy)", "press enter to (continue|confirm)",
  "enter to confirm", "select login method", "choose the text style", "sign in with", "log ?in to",
  "\\((y/n|yes/no)\\)", "\\[(y/n|yes/no)\\]",
].join("|"), "i");

/** Видимая часть экрана терминала — текстом (для CONFIRM_SCREEN). */
function screenText(t: Terminal): string {
  try {
    const buf = t.buffer.active;
    const lines: string[] = [];
    for (let i = buf.viewportY; i < buf.viewportY + t.rows; i++) {
      lines.push(buf.getLine(i)?.translateToString(true) ?? "");
    }
    return lines.join("\n");
  } catch {
    return "";
  }
}

/** Ссылка, ждущая вставки, для какой записи и номер просьбы. */
type Pending = { text: string; id: string; seq: number };
type PendView = "waiting" | "confirm" | "manual";
type UnsentReason = "browser" | "none" | "nothing" | "failed" | "exited";
type Unsent = { text: string; id: string; reason: UnsentReason };

const UNSENT_TEXT: Record<UnsentReason, string> = {
  browser: "Агент недоступен в браузере — ссылка не вставлена. Скопируйте её и задайте вопрос в окне приложения.",
  none: "Агент недоступен — ссылка не вставлена. Подключите Claude Code или Codex в настройках или скопируйте ссылку.",
  nothing: "Расшифровки пока нет, агенту нечего дать — ссылка не вставлена. Её можно скопировать.",
  failed: "Агент не запустился — ссылка не вставлена. Её можно скопировать и вставить после запуска.",
  exited: "Агент завершил работу раньше, чем ссылка была вставлена. Её можно скопировать.",
};

export type AgentProvider = { id: string; label: string };

const KNOWN: AgentProvider[] = [
  { id: "claude-code", label: "Claude Code" },
  { id: "codex", label: "Codex" },
];

/** Сценарий npm (codex.cmd), а не программа: встроенный терминал его не запускает. */
const SCRIPT = /\.(cmd|bat)$/i;

/**
 * Codex найден только как сценарий npm (`codex.cmd`): оболочка запускает лишь
 * codex.exe (аргументы через cmd.exe разбирались бы по его правилам).
 */
export function codexScriptOnly(info: AssistantInfo | null): boolean {
  const codex = info?.available?.codex;
  return !!codex?.found && SCRIPT.test(codex.path ?? "");
}

/** Как поставить Codex так, чтобы он запускался во вкладке. */
export const CODEX_SCRIPT_NOTE =
  "Codex установлен через npm (codex.cmd) — во встроенном терминале он не запускается. " +
  "Установите Codex отдельной программой: codex.exe в PATH или в папке " +
  "%LOCALAPPDATA%\\Programs\\OpenAI\\Codex\\bin.";

/** Агенты, которые можно запустить: установленные Claude Code и Codex (Codex — только как программа). */
export function agentProviders(info: AssistantInfo | null): AgentProvider[] {
  if (!info) return [];
  return KNOWN.filter((p) => info.available?.[p.id]?.found && !(p.id === "codex" && codexScriptOnly(info)));
}

/** Агент по умолчанию: тот, что выбран для итогов и вопросов, иначе первый установленный. */
export function defaultProvider(info: AssistantInfo | null, list: AgentProvider[]): string | null {
  return list.find((p) => p.id === info?.provider)?.id ?? list[0]?.id ?? null;
}

/** xterm грузится один раз, при первом открытии вкладки (StrictMode создаёт терминал дважды). */
let xterm: Promise<[typeof import("@xterm/xterm"), typeof import("@xterm/addon-fit")]> | null = null;
const loadXterm = () => (xterm ??= Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]));

/** Цвета и шрифт терминала — как у приложения. */
export const TERMINAL_OPTIONS: ITerminalOptions = {
  fontFamily: '"Cascadia Mono", Consolas, monospace',
  fontSize: 13,
  lineHeight: 1.15,
  cursorBlink: true,
  scrollback: 5000,
  theme: {
    background: "#0f1012",
    foreground: "#d6d7dc",
    cursor: "#5e6ad2",
    cursorAccent: "#0f1012",
    selectionBackground: "#5e6ad266",
  },
};

type Phase = "idle" | "starting" | "running" | "stopping" | "exited" | "error";
type AgentEvent = { id: string } & ({ data: string } | { code: number | null });

const EXIT_LINE = "\r\n\x1b[90m— агент завершил работу —\x1b[0m\r\n";

function phaseText(phase: Phase, code: number | null): string {
  switch (phase) {
    case "starting": return "Запуск…";
    case "running": return "Работает";
    case "stopping": return "Остановка…";
    case "exited": return code ? `Завершён (код ${code})` : "Завершён";
    default: return "Не запущен";
  }
}

/** Ctrl+Shift+C — копировать выделение, Ctrl+Shift+V — вставить; остальное — агенту. */
function clipboardKeys(term: Terminal) {
  return (e: KeyboardEvent): boolean => {
    if (e.type !== "keydown" || !e.ctrlKey || !e.shiftKey || e.altKey) return true;
    if (e.code === "KeyC") {
      e.preventDefault();
      const text = term.getSelection();
      if (text) void navigator.clipboard?.writeText(text).catch(() => {});
      return false;
    }
    if (e.code === "KeyV") {
      e.preventDefault();
      void pasteInto(term);
      return false;
    }
    return true;
  };
}

async function pasteInto(term: Terminal) {
  try {
    const text = await navigator.clipboard?.readText();
    if (text) term.paste(text);
  } catch {
    // Буфер недоступен — вставки нет, ошибки показывать не о чем.
  }
}

type Context = { files: string[]; live: boolean };

function contextText(ctx: Context): string {
  if (!ctx.files.length) return "Контекст: расшифровки пока нет";
  return `Контекст: ${ctx.files.join(" · ")}${ctx.live ? " — черновая лента живого режима" : ""}`;
}

/** Ссылка не вставлена (агента нет, не запустился, закрылся): её можно скопировать. */
function UnsentNote({ text, reason, onOpenSettings, onClose }: {
  text: string;
  reason: UnsentReason;
  onOpenSettings?: (section: string) => void;
  onClose: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const copyBtn = useRef<HTMLButtonElement>(null);
  // Фокус — на «Копировать»: реплика, с которой спросили, скрыта вместе с «Расшифровкой».
  useEffect(() => {
    const timer = setTimeout(() => copyBtn.current?.focus(), 0);
    return () => clearTimeout(timer);
  }, [text]);
  const copy = () => {
    navigator.clipboard?.writeText(text.trimEnd()).then(() => setCopied(true)).catch(() => {});
  };
  return (
    <div className="agent__unsent" role="status" aria-label="Ссылка не вставлена">
      <div>{UNSENT_TEXT[reason]}</div>
      <pre className="agent__ref">{text.trimEnd()}</pre>
      <div className="agent__unsent-row">
        <Button ref={copyBtn} onClick={copy}>{copied ? "Скопировано" : "Копировать"}</Button>
        {onOpenSettings && reason === "none" && (
          <Button onClick={() => onOpenSettings("assistant")}>Открыть настройки</Button>
        )}
        <button type="button" className="link-btn" onClick={onClose}>Скрыть</button>
      </div>
    </div>
  );
}

export function AgentTab({ id, assistant, onOpenSettings, endpoint, insert = null, onTaken }: {
  id: string;
  assistant: AssistantInfo | null;
  onOpenSettings?: (section: string) => void;
  /** Резидент: строка «Контекст» и «Прошлые вопросы». Нет — их нет. */
  endpoint?: Endpoint;
  /** «Спросить агента»: ссылка для поля ввода. */
  insert?: AgentInsert | null;
  /** Просьбу `insert` приняли: владелец её сбрасывает (вкладка, открытая заново, не вставит её ещё раз). */
  onTaken?: () => void;
}) {
  const shell = inTauri();
  const providers = agentProviders(assistant);
  const codexNote = codexScriptOnly(assistant);
  const [choice, setChoice] = useState<string | null>(null);
  const provider = providers.some((p) => p.id === choice) ? choice : defaultProvider(assistant, providers);
  const [phase, setPhase] = useState<Phase>("idle");
  const [code, setCode] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ready, setReady] = useState(false);
  /** Есть ли где показать терминал: в приложении и с установленным агентом. */
  const withScreen = shell && !(assistant && providers.length === 0);
  /** Вставить ссылку негде: браузер или ни одного агента. */
  const unavailable = !withScreen;

  /** Что получит агент (GET agent-context); null — ещё не знаем. */
  const [context, setContext] = useState<Context | null>(null);
  /** Ответ на запрос контекста пришёл (или запрос не удался — резидент старый). */
  const [contextTried, setContextTried] = useState(!endpoint);
  const contextSeq = useRef(0);
  const loadContext = useCallback(() => {
    if (!endpoint) return;
    const mine = ++contextSeq.current;
    getAgentContext(endpoint, id)
      .then((c) => { if (mine === contextSeq.current) setContext(c); })
      .catch(() => {})
      .finally(() => { if (mine === contextSeq.current) setContextTried(true); });
  }, [endpoint, id]);
  useEffect(() => {
    setContext(null);
    setContextTried(!endpoint);
    loadContext();
  }, [loadContext, endpoint]);
  /** Агенту пока нечего дать — запуск недоступен. */
  const nothing = context !== null && context.files.length === 0;

  /**
   * Ссылка, ждущая вставки, и для какой записи; `wanted` растёт с каждой
   * просьбой. Запись сменилась — ссылка чужая и не вставляется. (Не очищается
   * эффектом смены записи: в StrictMode эффекты запускаются дважды, и
   * просьба, принятая в первый раз, пропала бы.)
   */
  const [pend, setPend] = useState<Pending | null>(null);
  /** То же, что `pend`, для таймеров: вставка забирает ссылку ровно один раз. */
  const pending = useRef<Pending | null>(null);
  const setPending = useCallback((p: Pending | null) => { pending.current = p; setPend(p); }, []);
  /** Что видно в полосе ожидания: ждём агента, ждём подтверждения, вставить вручную. */
  const [pendView, setPendView] = useState<PendView>("waiting");
  const seq = useRef(0);
  const handled = useRef<AgentInsert | null>(null);
  /** Для какой просьбы агент уже запускался сам: упавший запуск не повторяется по кругу. */
  const autoStarted = useRef(0);
  const [unsent, setUnsent] = useState<Unsent | null>(null);
  /** Ссылка не дошла: в уведомление (её можно скопировать), из ожидания — вон. */
  const giveUp = useCallback((reason: UnsentReason) => {
    const p = pending.current;
    if (!p) return;
    setPending(null);
    setUnsent({ text: p.text, id: p.id, reason });
  }, [setPending]);
  /** Когда агент последний раз что-то вывел и когда сеанс заработал (Date.now). */
  const lastOutput = useRef(0);
  const root = useRef<HTMLDivElement>(null);

  const screen = useRef<HTMLDivElement>(null);
  const term = useRef<Terminal | null>(null);
  const fit = useRef<FitAddon | null>(null);
  /** Сеанс, чей вывод сейчас в терминале. */
  const session = useRef<string | null>(null);
  /** Номер запуска: ответ устаревшего запуска (перезапуск, другая запись) отбрасывается. */
  const attempt = useRef(0);
  /** События, пришедшие, пока запуск ещё не вернул id сеанса. */
  const early = useRef<AgentEvent[] | null>(null);

  const refit = useCallback(() => {
    const el = screen.current;
    if (!el || !fit.current || el.clientWidth === 0 || el.clientHeight === 0) return;
    try { fit.current.fit(); } catch { /* терминал ещё не отрисован */ }
  }, []);

  const apply = useCallback((e: AgentEvent) => {
    if ("data" in e) {
      lastOutput.current = Date.now();
      term.current?.write(e.data);
      return;
    }
    session.current = null;
    term.current?.write(EXIT_LINE);
    setCode(e.code);
    setPhase("exited");
    // Агент закрылся раньше, чем ссылка вставлена: она не теряется — в уведомление.
    giveUp("exited");
  }, [giveUp]);

  // Терминал: создаётся при первом открытии вкладки, живёт до закрытия карточки.
  useEffect(() => {
    if (!withScreen) return;
    let gone = false;
    let cleanup = () => {};
    void (async () => {
      const [{ Terminal }, { FitAddon }] = await loadXterm();
      const el = screen.current;
      if (gone || !el) return;
      const t = new Terminal(TERMINAL_OPTIONS);
      const f = new FitAddon();
      t.loadAddon(f);
      t.open(el);
      t.attachCustomKeyEventHandler(clipboardKeys(t));
      const input = t.onData((data) => {
        const sid = session.current;
        if (sid) agentWrite(sid, data).catch(() => {});
      });
      const resized = t.onResize(({ cols, rows }) => {
        const sid = session.current;
        if (sid) agentResize(sid, cols, rows).catch(() => {});
      });
      term.current = t;
      fit.current = f;
      // Скрытая вкладка — нулевой размер; подгонка при показе и при смене размера окна.
      const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => refit());
      observer?.observe(el);
      refit();
      cleanup = () => {
        observer?.disconnect();
        input.dispose();
        resized.dispose();
        t.dispose();
        term.current = null;
        fit.current = null;
        setReady(false);
        // Терминал убран (агентов на время не видно — список ещё не пришёл):
        // сеанс без экрана никто не увидит и не остановит — гасим его.
        attempt.current++;
        early.current = null;
        const sid = session.current;
        session.current = null;
        if (sid) {
          agentKill(sid).catch(() => {});
          setPhase("idle");
          setCode(null);
        }
      };
      setReady(true);
    })();
    return () => { gone = true; cleanup(); };
  }, [withScreen, refit]);

  // Вывод и конец сеанса: свой — в терминал, до ответа на запуск — в очередь.
  useEffect(() => {
    if (!shell) return;
    let live = true;
    const offs: Array<() => void> = [];
    const deliver = (e: AgentEvent) => {
      if (e.id === session.current) apply(e);
      else early.current?.push(e);
    };
    const keep = (off: () => void) => { if (live) offs.push(off); else off(); };
    void onAgentData(deliver).then(keep);
    void onAgentExit(deliver).then(keep);
    return () => { live = false; offs.forEach((off) => off()); };
  }, [shell, apply]);

  // Другая запись или закрытая карточка — агент останавливается.
  useEffect(() => {
    setPhase("idle");
    setCode(null);
    setError(null);
    term.current?.reset();
    return () => {
      attempt.current++;
      early.current = null;
      const sid = session.current;
      session.current = null;
      if (sid) agentKill(sid).catch(() => {});
    };
  }, [id]);

  const start = async () => {
    const t = term.current;
    if (!provider || !t) return;
    const old = session.current;
    session.current = null;
    if (old) agentKill(old).catch(() => {});
    const mine = ++attempt.current;
    early.current = [];
    t.reset();
    refit();
    setPhase("starting");
    setCode(null);
    setError(null);
    try {
      const sid = await agentSpawn(id, provider, t.cols, t.rows);
      if (mine !== attempt.current) {
        agentKill(sid).catch(() => {});
        return;
      }
      session.current = sid;
      // Запуск обновил файлы встречи — строка «Контекст» тоже.
      loadContext();
      lastOutput.current = Date.now();
      setPhase("running");
      const queued = early.current ?? [];
      early.current = null;
      queued.filter((e) => e.id === sid).forEach(apply);
      term.current?.focus();
    } catch (e) {
      if (mine !== attempt.current) return;
      early.current = null;
      setPhase("error");
      setError(errorText(e));
      giveUp("failed");
    }
  };

  // Просьба «Спросить агента»: ссылка ждёт вставки; одна и та же просьба — один раз.
  // Несколько просьб, пока агент не готов, — все ссылки подряд, ни одна не теряется.
  useEffect(() => {
    if (!insert || insert === handled.current) return;
    handled.current = insert;
    if (!insert.text) return;
    const before = pending.current?.id === id ? pending.current.text.trimEnd() : "";
    setPending({ text: before ? `${before}\n${insert.text}` : insert.text, id, seq: ++seq.current });
    setPendView("waiting");
    setUnsent(null);
    loadContext();
    onTaken?.();
    // Фокус — сразу во вкладку (терминал или сама вкладка): прежний элемент
    // (реплика) скрыт вместе с «Расшифровкой». После переключения вкладки.
    setTimeout(() => {
      if (term.current) term.current.focus();
      else root.current?.focus();
    }, 0);
  }, [insert, loadContext, setPending, onTaken]);

  // Вставить некуда — ссылка в уведомление (её можно скопировать).
  useEffect(() => {
    if (!pend || pend.id !== id) return;
    if (unavailable) giveUp(shell ? "none" : "browser");
    else if (nothing) giveUp("nothing");
  }, [pend, id, unavailable, nothing, shell, giveUp]);

  // Агент не запущен — запускаем сами (один раз на просьбу; упавший запуск — кнопкой).
  useEffect(() => {
    if (!pend || pend.id !== id || unavailable || !ready || !provider || !contextTried || nothing) return;
    if (autoStarted.current === pend.seq || (phase !== "idle" && phase !== "exited")) return;
    autoStarted.current = pend.seq;
    void start();
  });

  // Ссылку забрали: вставка в поле ввода, всегда одной строкой (pasteLine) — ни \r,
  // ни \n, ни ESC. Ровно один раз: ссылка уходит из ожидания до вставки.
  const deliver = useCallback(() => {
    const t = term.current;
    const p = pending.current;
    if (!t || !p || p.id !== id || !session.current) return;
    setPending(null);
    t.paste(pasteLine(p.text));
    t.focus();
  }, [id, setPending]);

  // Сеанс работает — вставка, когда агент готов принять текст: включил режим
  // вставки, вывод затих на QUIET_MS, и на экране не вопрос первого запуска
  // («доверять ли папке», вход, выбор темы). Такой вопрос виден — ждём, пока
  // человек ответит. Признаков готовности нет за PASTE_WAIT_MS — кнопка
  // «Вставить реплику»: вслепую не вставляем.
  const pendSeq = pend?.id === id ? pend.seq : 0;
  useEffect(() => {
    if (phase !== "running" || !pendSeq) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let began = Date.now();
    const tick = () => {
      const t = term.current;
      if (!t || !pending.current || !session.current) return;
      const now = Date.now();
      if (CONFIRM_SCREEN.test(screenText(t))) {
        setPendView("confirm");
        began = now;
      } else {
        setPendView("waiting");
        const bracketed = t.modes?.bracketedPasteMode === true;
        if (bracketed && now - lastOutput.current >= QUIET_MS) {
          deliver();
          return;
        }
        if (now - began >= PASTE_WAIT_MS) {
          setPendView("manual");
          return;
        }
      }
      timer = setTimeout(tick, POLL_MS);
    };
    tick();
    return () => clearTimeout(timer);
  }, [phase, pendSeq, deliver]);

  const stop = () => {
    const sid = session.current;
    if (!sid) return;
    setPhase("stopping");
    agentKill(sid).catch((e) => setError(errorText(e)));
  };

  // Правая кнопка: есть выделение — копировать, нет — вставить (как в терминале Windows).
  const onContextMenu = (e: MouseEvent) => {
    const t = term.current;
    if (!t) return;
    e.preventDefault();
    if (t.hasSelection()) {
      void navigator.clipboard?.writeText(t.getSelection()).catch(() => {});
      t.clearSelection();
    } else {
      void pasteInto(t);
    }
  };

  const unsentNote = unsent?.id === id && (
    <UnsentNote text={unsent.text} reason={unsent.reason} onOpenSettings={onOpenSettings}
      onClose={() => setUnsent(null)} />
  );
  // Ссылка ждёт вставки: видно, чего ждём; вручную — только когда признаков готовности нет.
  const waiting = pend?.id === id && !unavailable && !nothing && (
    <div className="agent__pending" role="status" aria-label="Ссылка ждёт вставки">
      <span>
        {pendView === "confirm" ? "Подтвердите запуск агента — реплика будет вставлена после."
          : pendView === "manual" ? "Агент не сообщил, что готов принять текст. Вставьте реплику, когда поле ввода будет видно."
            : "Реплика будет вставлена в поле ввода, когда агент будет готов."}
      </span>
      {pendView === "manual" && (
        <Button variant="primary" onClick={deliver} disabled={phase !== "running"}>Вставить реплику</Button>
      )}
      <button type="button" className="link-btn" onClick={() => setPending(null)}>Отменить</button>
    </div>
  );
  const past = endpoint ? <PastQuestions endpoint={endpoint} id={id} /> : null;

  if (!shell) {
    return (
      <div className="agent agent--empty" ref={root} tabIndex={-1}>
        {unsentNote}
        <EmptyState title="Доступно в приложении"
          hint="Терминал с Claude Code или Codex работает только в окне приложения Meet, в браузере его нет." />
        {past}
      </div>
    );
  }
  if (assistant && providers.length === 0) {
    return (
      <div className="agent agent--empty" ref={root} tabIndex={-1}>
        {unsentNote}
        <EmptyState title="Подключите Claude Code или Codex в настройках"
          hint={codexNote ? CODEX_SCRIPT_NOTE : "Во вкладке запускается агент, установленный на компьютере."}
          action={onOpenSettings && <Button onClick={() => onOpenSettings("assistant")}>Открыть настройки</Button>} />
        {past}
      </div>
    );
  }

  const active = phase === "starting" || phase === "running" || phase === "stopping";
  return (
    <div className="agent" ref={root} tabIndex={-1}>
      <div className="agent__bar">
        <label className="agent__label" htmlFor={`agent-provider-${id}`}>Агент</label>
        <select id={`agent-provider-${id}`} className="agent__select" value={provider ?? ""}
          disabled={!providers.length || phase === "starting"} onChange={(e) => setChoice(e.target.value)}>
          {providers.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
        </select>
        <HelpTip label="Что такое вкладка «Агент»" title="Агент в папке встречи">
          <TipLine>
            Здесь работает Claude Code или Codex — тот же, что в обычном терминале: можно задавать вопросы по встрече,
            просить черновик письма или сверить договорённости с базой знаний.
          </TipLine>
          <TipLine>
            Агент запускается в папке встречи. Перед каждым запуском приложение обновляет transcript.md — расшифровку
            с именами и таймкодами; рядом лежат итоги (summary.md) и разметка встречи (analysis.json), если они есть.
            Пока идёт запись с ассистентом, transcript.md — черновая лента живого режима.
          </TipLine>
          <TipLine>
            Кнопка со звёздочками у реплики, пункта итогов или подсказки ассистента открывает эту вкладку и вставляет
            ссылку на них в поле ввода агента: допишите вопрос и нажмите Enter. У реплики то же — клавиша A.
          </TipLine>
          <TipLine>
            Агент может читать файлы, создавать их в папке встречи и выполнять команды. Что требует подтверждения,
            определяют настройки самого агента.
          </TipLine>
          <TipLine>Копировать — Ctrl+Shift+C, вставить — Ctrl+Shift+V или правой кнопкой мыши.</TipLine>
          <TipLine>Агент останавливается, когда вы закрываете карточку встречи или окно приложения.</TipLine>
        </HelpTip>
        {active ? (
          <>
            <Button onClick={() => void start()} disabled={!ready || !provider || phase === "starting"}>Перезапустить</Button>
            <Button onClick={stop} disabled={phase !== "running"}>Остановить</Button>
          </>
        ) : (
          <Button variant="primary" onClick={() => void start()} disabled={!ready || !provider || nothing}>
            Запустить
          </Button>
        )}
        <span className="agent__phase" role="status">
          <span className={`agent__dot${phase === "running" ? " agent__dot--run" : ""}`} aria-hidden="true" />
          {phaseText(phase, code)}
        </span>
      </div>
      {context && <div className="agent__context">{contextText(context)}</div>}
      <div className="agent__hint">
        {active ? "Агент запущен в папке встречи" : "Агент откроется в папке встречи"}. База знаний подключена для
        чтения; права на запись определяются настройками агента.
      </div>
      {codexNote && <div className="agent__hint">{CODEX_SCRIPT_NOTE}</div>}
      {error && <div className="assist__error" role="alert">{error}</div>}
      {waiting}
      {unsentNote}
      <div className="agent__screen" onContextMenu={onContextMenu}>
        <div className="agent__xterm" ref={screen} data-agent-terminal />
        {phase === "idle" && (
          <div className="agent__idle">
            {nothing ? "Агент станет доступен, когда появится расшифровка."
              : "Нажмите «Запустить» — агент откроется в папке этой встречи."}
          </div>
        )}
      </div>
      {!active && past}
    </div>
  );
}
