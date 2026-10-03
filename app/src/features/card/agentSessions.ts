/**
 * Сеансы вкладки «Агент» — по одному на запись, вне React: терминал xterm.js,
 * процесс агента в оболочке (Rust, псевдоконсоль) и ссылка, ждущая вставки,
 * живут здесь, а вкладка (AgentTab) только показывает сеанс своей записи.
 *
 * Поэтому сеанс переживает переключение вкладок карточки, переход к другой
 * записи, в «Голоса» и «Настройки»: терминал (с прокруткой) отсоединяется от
 * карточки и присоединяется снова, когда вкладку «Агент» этой записи открывают
 * опять. Кончается сеанс «Остановить», выходом самого агента, удалением записи
 * и закрытием окна (оболочка гасит всех агентов) — следующий раз предложит
 * «Продолжить прошлую».
 *
 * Одновременно работают не больше MAX_SESSIONS агентов: новый запуск закрывает
 * давно не открывавшийся сеанс, который сейчас ничего не делает (не выводил
 * BUSY_MS, нет ссылки, ждущей вставки, не на экране); занятый не закрывается
 * никогда — тогда агентов на время больше. Закрытый так сеанс помнит это:
 * его вкладка скажет «Сессия была закрыта, чтобы освободить ресурсы» и
 * предложит «Продолжить прошлую».
 *
 * Вывод оболочки (`agent-data`, `agent-exit`) слушается один раз на окно и
 * уходит сеансу по его id, поэтому ссылка «Спросить агента» попадает в агента
 * своей записи, даже если на экране другая.
 */

import { useSyncExternalStore } from "react";
import type { ITerminalOptions, Terminal } from "@xterm/xterm";
import type { FitAddon } from "@xterm/addon-fit";
import { joinPrompts, pasteLine } from "../../lib/agentRef";
import { errorText } from "../../lib/format";
import { agentKill, agentResize, agentSpawn, agentWrite, onAgentData, onAgentExit } from "../../lib/shell";
import { PASTE_WAIT_MS, POLL_MS, QUIET_MS, coldReadiness, ownTitle, screenOutput, screenRows } from "./agentReady";

/** Сколько агентов работает одновременно, пока ни один не занят. */
export const MAX_SESSIONS = 3;
/** Агент выводил что-то так недавно — занят (отвечает): сам он не закрывается. */
export const BUSY_MS = 5_000;
/** «Остановить» спрашивает, если агент выводил что-то так недавно. */
export const STOP_CONFIRM_MS = 10_000;

export type Phase = "idle" | "starting" | "running" | "stopping" | "exited" | "error";
export type PendView = "waiting" | "confirm" | "manual";
export type UnsentReason = "browser" | "none" | "nothing" | "failed" | "exited" | "cancelled";
type AgentEvent = { id: string } & ({ data: string } | { code: number | null });

/**
 * Ссылка, ждущая вставки: номер просьбы, когда был первый щелчок (срок не
 * сдвигается) и запустила ли агента сама просьба (тогда — ждать готовности).
 */
type Pending = { text: string; seq: number; at: number; cold: boolean };

/** Что видно во вкладке: снимок сеанса (новый объект — что-то изменилось). */
export type AgentView = {
  /** Терминал создан. */
  ready: boolean;
  phase: Phase;
  code: number | null;
  error: string | null;
  /** Ссылка ждёт вставки: что видно в полосе и номер просьбы. */
  pending: { text: string; seq: number; view: PendView } | null;
  /** Ссылка не вставлена — в уведомлении, её можно скопировать. */
  unsent: { text: string; reason: UnsentReason } | null;
  /** Сеанс закрыт, чтобы освободить ресурсы: каким агентом продолжить. */
  evicted: { provider: string } | null;
};

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
    // xterm 6 рисует свою полосу прокрутки (не нативную): цвета — как --sb-thumb* в theme/tokens.css.
    scrollbarSliderBackground: "rgba(138, 140, 150, 0.28)",
    scrollbarSliderHoverBackground: "rgba(138, 140, 150, 0.62)",
    scrollbarSliderActiveBackground: "rgba(138, 140, 150, 0.8)",
  },
};

const EXIT_LINE = "\r\n\x1b[90m— агент завершил работу —\x1b[0m\r\n";

/** xterm грузится один раз, при первом открытии вкладки «Агент». */
let xterm: Promise<[typeof import("@xterm/xterm"), typeof import("@xterm/addon-fit")]> | null = null;
const loadXterm = () => (xterm ??= Promise.all([import("@xterm/xterm"), import("@xterm/addon-fit")]));

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

export async function pasteInto(term: Terminal) {
  try {
    const text = await navigator.clipboard?.readText();
    if (text) term.paste(text);
  } catch {
    // Буфер недоступен — вставки нет, ошибки показывать не о чем.
  }
}

export class AgentSession {
  readonly id: string;
  term: Terminal | null = null;
  private fit: FitAddon | null = null;
  /** Свой элемент терминала: переходит из карточки в карточку. */
  private el: HTMLDivElement | null = null;
  private opened = false;
  private creating: Promise<void> | null = null;
  /** Куда терминал сейчас вставлен (вкладка «Агент» на экране) — или никуда. */
  private host: HTMLElement | null = null;
  private observer: ResizeObserver | null = null;
  private listeners = new Set<() => void>();
  private snap: AgentView;

  /** Сеанс оболочки, чей вывод сейчас в терминале. */
  session: string | null = null;
  /** Агент сеанса (Claude Code, Codex). */
  provider: string | null = null;
  phase: Phase = "idle";
  private code: number | null = null;
  private error: string | null = null;
  /** Номер запуска: ответ устаревшего запуска (перезапуск, остановка) отбрасывается. */
  private attempt = 0;
  /** События, пришедшие, пока запуск ещё не вернул id сеанса. */
  private early: AgentEvent[] | null = null;
  /** Когда агент последний раз менял экран (не только заголовок окна) или запустился, Date.now: тишина для вставки. */
  private lastOutput = 0;
  /** Когда агент последний раз что-то вывел на экран (0 — ещё ничего): занят ли он. */
  lastData = 0;
  /** Поставил ли агент свой заголовок окна (готовность Codex). */
  private titled = false;
  /** Сеанс хоть раз был готов принять текст: диалог первого запуска больше не проверяется. */
  private sessionReady = false;
  /** Когда сеанс последний раз открывали или трогали — для выбора, кого закрыть. */
  lastUsed = Date.now();

  private pend: Pending | null = null;
  private pendView: PendView = "waiting";
  private overdue = false;
  private seq = 0;
  /** Для какой просьбы агент уже запускался сам: упавший запуск не повторяется по кругу. */
  autoStarted = 0;
  private unsent: AgentView["unsent"] = null;
  private evicted: AgentView["evicted"] = null;
  private tickTimer: ReturnType<typeof setTimeout> | undefined;
  private overdueTimer: ReturnType<typeof setTimeout> | undefined;

  constructor(id: string) {
    this.id = id;
    this.snap = this.view();
  }

  // --- подписка (useSyncExternalStore) ---------------------------------------

  subscribe = (fn: () => void) => {
    this.listeners.add(fn);
    return () => { this.listeners.delete(fn); };
  };

  snapshot = () => this.snap;

  private view(): AgentView {
    const p = this.pend;
    return {
      ready: this.term !== null,
      phase: this.phase,
      code: this.code,
      error: this.error,
      pending: p && { text: p.text, seq: p.seq, view: this.overdue ? "manual" : this.pendView },
      unsent: this.unsent,
      evicted: this.evicted,
    };
  }

  private notify() {
    this.snap = this.view();
    this.listeners.forEach((fn) => fn());
    changed();
  }

  // --- терминал ---------------------------------------------------------------

  /**
   * Показать терминал в `container` (вкладка «Агент» на экране). Возвращает,
   * как его оттуда убрать: терминал и сеанс при этом не гаснут.
   */
  attach(container: HTMLElement): () => void {
    this.host = container;
    this.lastUsed = Date.now();
    if (this.el) this.mount();
    else void this.create();
    return () => this.detach(container);
  }

  private detach(container: HTMLElement) {
    if (this.host !== container) return;
    this.host = null;
    this.observer?.disconnect();
    this.observer = null;
    if (this.el?.parentNode === container) container.removeChild(this.el);
    this.lastUsed = Date.now();
    // Сеанса не было и ничего не ждёт — терминал не нужен (вкладку открывали и
    // только). Не сразу: StrictMode тут же присоединяет вкладку снова.
    setTimeout(() => {
      if (!this.host && this.phase === "idle" && !this.pend && !this.unsent && !this.evicted) forget(this);
    }, 0);
  }

  private create() {
    this.creating ??= (async () => {
      const [{ Terminal }, { FitAddon }] = await loadXterm();
      if (sessions.get(this.id) !== this) return;
      const t = new Terminal(TERMINAL_OPTIONS);
      const f = new FitAddon();
      t.loadAddon(f);
      t.attachCustomKeyEventHandler(clipboardKeys(t));
      t.onData((data) => {
        this.lastUsed = Date.now();
        if (this.session) agentWrite(this.session, data).catch(() => {});
      });
      t.onResize(({ cols, rows }) => {
        if (this.session) agentResize(this.session, cols, rows).catch(() => {});
      });
      t.onTitleChange((text) => { if (ownTitle(text)) this.titled = true; });
      const el = document.createElement("div");
      el.className = "agent__term";
      this.term = t;
      this.fit = f;
      this.el = el;
      if (this.host) this.mount();
      this.notify();
    })();
    return this.creating;
  }

  /** Терминал — в текущее место на экране; открыт впервые — там же. */
  private mount() {
    const host = this.host;
    const el = this.el;
    const t = this.term;
    if (!host || !el || !t) return;
    if (el.parentNode !== host) host.appendChild(el);
    if (!this.opened) {
      t.open(el);
      this.opened = true;
    }
    // Скрытая вкладка — нулевой размер; подгонка при показе и при смене размера окна.
    this.observer?.disconnect();
    this.observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => this.refit());
    this.observer?.observe(host);
    this.refit();
    try { t.refresh(0, t.rows - 1); } catch { /* терминал ещё не отрисован */ }
  }

  refit() {
    const el = this.el;
    if (!el || !this.host || !this.fit || el.clientWidth === 0 || el.clientHeight === 0) return;
    try { this.fit.fit(); } catch { /* терминал ещё не отрисован */ }
  }

  focus() {
    if (this.host) this.term?.focus();
  }

  /** Терминал сеанса сейчас на экране (вкладка «Агент» его записи открыта). */
  shown(): boolean {
    return this.host !== null;
  }

  // --- запуск и остановка -----------------------------------------------------

  isLive(): boolean {
    return this.phase === "starting" || this.phase === "running" || this.phase === "stopping";
  }

  /** Занят: запускается, ждёт вставки или недавно что-то выводил. Такой сам не закрывается. */
  busy(now = Date.now()): boolean {
    return this.phase === "starting" || this.pend !== null || now - this.lastData < BUSY_MS;
  }

  /** `resume` — «Продолжить прошлую»: последний разговор агента в папке встречи. */
  async start(provider: string, resume = false): Promise<boolean> {
    const t = this.term;
    if (!t) return false;
    const old = this.session;
    this.session = null;
    if (old) agentKill(old).catch(() => {});
    const mine = ++this.attempt;
    this.early = [];
    t.reset();
    this.refit();
    this.provider = provider;
    this.titled = false;
    this.sessionReady = false;
    this.evicted = null;
    this.lastUsed = Date.now();
    this.phase = "starting";
    this.code = null;
    this.error = null;
    this.notify();
    makeRoom(this);
    try {
      await listen();
      if (mine !== this.attempt) return false;
      const sid = await agentSpawn(this.id, provider, t.cols, t.rows, resume);
      if (mine !== this.attempt) {
        agentKill(sid).catch(() => {});
        return false;
      }
      this.session = sid;
      this.lastOutput = Date.now();
      this.phase = "running";
      const queued = this.early ?? [];
      this.early = null;
      queued.filter((e) => e.id === sid).forEach((e) => this.apply(e));
      this.focus();
      this.notify();
      this.schedule();
      return true;
    } catch (e) {
      if (mine !== this.attempt) return false;
      this.early = null;
      this.phase = "error";
      this.error = errorText(e);
      this.notify();
      this.giveUp("failed");
      return false;
    }
  }

  stop() {
    const sid = this.session;
    if (!sid) return;
    this.phase = "stopping";
    this.notify();
    agentKill(sid).catch((e) => {
      this.error = errorText(e);
      this.notify();
    });
  }

  /** Закрыть, чтобы освободить место (makeRoom): вкладка потом предложит продолжить. */
  evict() {
    const sid = this.session;
    if (!sid || !this.provider) return;
    this.evicted = { provider: this.provider };
    this.stop();
  }

  dismissEvicted() {
    if (!this.evicted) return;
    this.evicted = null;
    this.notify();
  }

  /** Событие оболочки: своё — в терминал, до ответа на запуск — в очередь. */
  event(e: AgentEvent) {
    if (e.id === this.session) this.apply(e);
    else this.early?.push(e);
  }

  private apply(e: AgentEvent) {
    if ("data" in e) {
      if (screenOutput(e.data)) this.lastOutput = this.lastData = Date.now();
      this.term?.write(e.data);
      return;
    }
    this.session = null;
    this.sessionReady = false;
    this.term?.write(EXIT_LINE);
    this.code = e.code;
    this.phase = "exited";
    this.notify();
    // Агент закрылся раньше, чем ссылка вставлена: она не теряется — в уведомление.
    this.giveUp("exited");
  }

  // --- ссылка «Спросить агента» -------------------------------------------------

  /**
   * Новая просьба: ссылка ждёт вставки. Несколько просьб, пока агент не готов, —
   * все ссылки подряд, ни одна не теряется. Агент ещё не работает — эту
   * просьбу запустит (или дождётся) запуск: тогда ждём готовности.
   */
  request(text: string) {
    const prev = this.pend;
    const cold = this.phase !== "running";
    this.pend = {
      text: prev ? joinPrompts([prev.text, text]) : text, seq: ++this.seq,
      at: prev?.at ?? Date.now(), cold: (prev?.cold ?? false) || cold,
    };
    if (!prev) {
      this.pendView = "waiting";
      this.overdue = false;
      clearTimeout(this.overdueTimer);
      // Один срок от первого щелчка, не сдвигается: дальше — «Вставить ссылку».
      this.overdueTimer = setTimeout(() => {
        if (!this.pend) return;
        this.overdue = true;
        this.notify();
      }, PASTE_WAIT_MS);
    }
    this.unsent = null;
    this.lastUsed = Date.now();
    this.notify();
    this.schedule();
  }

  /**
   * Ссылку забрали: вставка в поле ввода, всегда одной строкой (pasteLine) — ни
   * \r, ни \n, ни ESC. Ровно один раз: ссылка уходит из ожидания до вставки.
   */
  deliver() {
    const t = this.term;
    const p = this.pend;
    if (!t || !p || !this.session) return;
    this.clearPending();
    t.paste(pasteLine(p.text));
    this.focus();
    this.notify();
  }

  /** Ссылка не дошла: в уведомление (её можно скопировать), из ожидания — вон. */
  giveUp(reason: UnsentReason) {
    const p = this.pend;
    if (!p) return;
    this.clearPending();
    this.unsent = { text: p.text, reason };
    this.notify();
  }

  dismissUnsent() {
    if (!this.unsent) return;
    this.unsent = null;
    this.notify();
  }

  private clearPending() {
    this.pend = null;
    clearTimeout(this.overdueTimer);
    clearTimeout(this.tickTimer);
    this.tickTimer = undefined;
  }

  /**
   * Сеанс работает и ссылка ждёт — вставка. Сеанс уже был готов или работал к
   * моменту щелчка — сразу. Запуск начала сама просьба (cold) и сеанс ещё ни
   * разу не был готов — ждём готовности (coldReadiness): режим вставки
   * включён, видно поле ввода агента, у Codex — его заголовок окна, нигде на
   * экране нет диалога (тогда — «Подтвердите запуск агента…»), и экран не
   * менялся QUIET_MS. Однажды готовый сеанс диалог больше не проверяет.
   * Работает и без вкладки на экране (другая запись, другой раздел).
   */
  private schedule() {
    if (this.tickTimer !== undefined || this.phase !== "running" || !this.pend) return;
    const tick = () => {
      this.tickTimer = undefined;
      const t = this.term;
      const p = this.pend;
      if (!t || !p || !this.session || this.phase !== "running") return;
      if (!p.cold || this.sessionReady) {
        this.deliver();
        return;
      }
      const state = coldReadiness(screenRows(t), {
        bracketed: t.modes?.bracketedPasteMode === true, provider: this.provider, titled: this.titled,
      });
      const view: PendView = state === "confirm" ? "confirm" : "waiting";
      if (view !== this.pendView) {
        this.pendView = view;
        this.notify();
      }
      if (state === "ready" && Date.now() - this.lastOutput >= QUIET_MS) {
        this.sessionReady = true;
        this.deliver();
        return;
      }
      this.tickTimer = setTimeout(tick, POLL_MS);
    };
    tick();
  }

  /**
   * Сеанс больше не нужен (forget) или сброс (тесты): таймеры — прочь, терминал
   * закрыть; процесс не трогаем. Открытая вкладка узнаёт об этом и берёт новый
   * сеанс записи (`quiet` — не сообщать: тесты, вкладок уже нет).
   */
  dispose(quiet = false) {
    this.attempt++;
    this.clearPending();
    this.observer?.disconnect();
    this.el?.remove();
    this.term?.dispose();
    this.term = null;
    if (!quiet) {
      this.snap = this.view();
      this.listeners.forEach((fn) => fn());
    }
    this.listeners.clear();
  }
}

// --- все сеансы окна ---------------------------------------------------------------

const sessions = new Map<string, AgentSession>();
const anyListeners = new Set<() => void>();

function changed() {
  anyListeners.forEach((fn) => fn());
}

function forget(s: AgentSession) {
  if (sessions.get(s.id) !== s) return;
  sessions.delete(s.id);
  s.dispose();
  changed();
}

/** Сеанс записи (создаётся при первом обращении). */
export function agentSession(id: string): AgentSession {
  let s = sessions.get(id);
  if (!s) {
    s = new AgentSession(id);
    sessions.set(id, s);
  }
  return s;
}

/** Подписка на любые изменения сеансов (точка «агент работает» в списке и карточке). */
export function subscribeAgents(fn: () => void) {
  anyListeners.add(fn);
  return () => { anyListeners.delete(fn); };
}

/** Работает ли агент записи (запускается, работает, останавливается). */
export function agentLive(id: string): boolean {
  return sessions.get(id)?.isLive() ?? false;
}

/** Работает ли агент записи — для точки «агент работает» в списке записей и на вкладке «Агент». */
export function useAgentLive(id: string): boolean {
  return useSyncExternalStore(subscribeAgents, () => agentLive(id));
}

/**
 * Перед запуском `starting`: работающих уже MAX_SESSIONS — закрыть давно не
 * открывавшиеся из тех, что ничего не делают и не на экране. Занятых не
 * трогаем: лучше на время больше агентов, чем оборванный ответ.
 */
function makeRoom(starting: AgentSession) {
  const now = Date.now();
  const live = [...sessions.values()].filter((s) => s !== starting && s.isLive() && s.phase !== "stopping");
  const extra = live.length - (MAX_SESSIONS - 1);
  if (extra <= 0) return;
  live
    .filter((s) => s.phase === "running" && !s.busy(now) && !s.shown())
    .sort((a, b) => a.lastUsed - b.lastUsed)
    .slice(0, extra)
    .forEach((s) => s.evict());
}

/** Вывод оболочки — один раз на окно; каждому сеансу его события. */
let listening: Promise<void> | null = null;
let unlisten: Array<() => void> = [];
function listen(): Promise<void> {
  listening ??= (async () => {
    const route = (e: AgentEvent) => sessions.forEach((s) => s.event(e));
    unlisten = await Promise.all([onAgentData(route), onAgentExit(route)]);
  })().catch((e: unknown) => {
    listening = null;
    throw e;
  });
  return listening;
}

/** Тесты: каждый начинает без сеансов и подписок прошлого (зовёт test/setup.ts). */
export function resetAgentSessions() {
  sessions.forEach((s) => s.dispose(true));
  sessions.clear();
  anyListeners.clear();
  unlisten.forEach((off) => off());
  unlisten = [];
  listening = null;
}
if (import.meta.env?.MODE === "test") {
  (globalThis as { __resetAgentSessions?: () => void }).__resetAgentSessions = resetAgentSessions;
}
