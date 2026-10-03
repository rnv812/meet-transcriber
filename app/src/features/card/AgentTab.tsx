/**
 * Вкладка «Агент»: настоящий Claude Code или Codex во встроенном терминале,
 * запущенный в папке встречи (там transcript.md и summary.md).
 *
 * Терминал — xterm.js; процесс агента живёт в оболочке (Rust, псевдоконсоль):
 * ввод уходит командой `agent_write`, вывод приходит событиями `agent-data`.
 * Сам сеанс — терминал, процесс и ссылка, ждущая вставки, — живёт вне вкладки
 * (agentSessions), по одному на запись: вкладка его только показывает. Поэтому
 * он переживает переключение вкладок карточки, переход к другой записи, в
 * «Голоса» и «Настройки»; кончается «Остановить» (с вопросом, если агент
 * только что что-то выводил), выходом агента и закрытием окна. Работающих
 * агентов не больше трёх: лишний давно не открывавшийся и ничего не делающий
 * закрывается, и его вкладка предложит «Продолжить прошлую».
 *
 * «Спросить агента» (✦ у реплики, пункта итогов, подсказки ассистента)
 * приходит пропом `insert`: вкладка запускает агента, если он не запущен, и
 * вставляет ссылку в поле ввода так же, как вставку из буфера (`term.paste`),
 * всегда одной строкой (`pasteLine`: ни \r, ни \n, ни ESC — Enter вставка не
 * нажмёт). Работающему агенту — сразу. Агента запускает сама просьба — ждём,
 * пока он впервые готов: включил режим bracketed paste, видно его поле ввода,
 * экран затих, и на экране нет диалога («доверять ли папке», вопрос об
 * обновлении — тогда подсказка «Подтвердите запуск агента…» с «Вставить
 * сейчас»). Режима вставки и тишины мало: оба агента включают его сразу при
 * старте и молчат, пока готовят сеанс (см. agentReady). Срок от щелчка не
 * сдвигается: после него — «Вставить ссылку». «Отменить», неудачный запуск,
 * выход агента, отсутствие агента — ссылку показывает уведомление, её можно
 * скопировать.
 */

import { useCallback, useEffect, useRef, useState, useSyncExternalStore, type MouseEvent } from "react";
import "@xterm/xterm/css/xterm.css";
import { getAgentContext, type Endpoint } from "../../lib/api";
import { inTauri } from "../../lib/shell";
import type { AssistantInfo } from "../../lib/types";
import { Button } from "../../ui/Button";
import { ConfirmDialog } from "../../ui/ConfirmDialog";
import { EmptyState } from "../../ui/EmptyState";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import {
  STOP_CONFIRM_MS, agentSession, pasteInto, type PendView, type Phase, type UnsentReason,
} from "./agentSessions";
import { PastQuestions } from "./PastQuestions";
import "./agent.css";

export {
  CONFIRM_SCREEN, PASTE_WAIT_MS, QUIET_MS, coldReadiness, ownTitle, promptVisible, screenOutput, screenRows,
} from "./agentReady";
export { MAX_SESSIONS, TERMINAL_OPTIONS } from "./agentSessions";

/** Ссылка для поля ввода агента (lib/agentRef). Новый объект — новая просьба. */
export type AgentInsert = { text: string };

/** Просьбы, которые уже приняла какая-нибудь вкладка: одна и та же — один раз, и только в свою запись. */
const taken = new WeakSet<AgentInsert>();

const UNSENT_TEXT: Record<UnsentReason, string> = {
  browser: "Агент недоступен в браузере — ссылка не вставлена. Скопируйте её и задайте вопрос в окне приложения.",
  none: "Агент недоступен — ссылка не вставлена. Подключите Claude Code или Codex в настройках или скопируйте ссылку.",
  nothing: "Расшифровки пока нет, агенту нечего дать — ссылка не вставлена. Её можно скопировать.",
  failed: "Агент не запустился — ссылка не вставлена. Её можно скопировать и вставить после запуска.",
  exited: "Агент завершил работу раньше, чем ссылка была вставлена. Её можно скопировать.",
  cancelled: "Вставка отменена. Ссылку можно скопировать.",
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

function phaseText(phase: Phase, code: number | null): string {
  switch (phase) {
    case "starting": return "Запуск…";
    case "running": return "Работает";
    case "stopping": return "Остановка…";
    case "exited": return code ? `Завершён (код ${code})` : "Завершён";
    default: return "Не запущен";
  }
}

type Context = { files: string[]; live: boolean; sessions?: string[] };

function contextText(ctx: Context): string {
  if (!ctx.files.length) return "Контекст: расшифровки пока нет";
  return `Контекст: ${ctx.files.join(" · ")}${ctx.live ? " — черновая лента живого режима" : ""}`;
}

const PENDING_TEXT: Record<PendView, string> = {
  waiting: "Ссылка будет вставлена в поле ввода, когда агент будет готов.",
  confirm: "Подтвердите запуск агента — ссылка будет вставлена после.",
  manual: "Агент пока не сообщил, что готов принять текст. Вставьте ссылку, когда поле ввода будет видно, или скопируйте её.",
};

/** Полоса над терминалом: ссылка ждёт вставки. */
function PendingStrip({ text, view, canInsert, onInsert, onCancel }: {
  text: string;
  view: PendView;
  canInsert: boolean;
  onInsert: () => void;
  onCancel: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard?.writeText(text.trimEnd()).then(() => setCopied(true)).catch(() => {});
  };
  return (
    <div className="agent__pending" role="status" aria-label="Ссылка ждёт вставки">
      <span>{PENDING_TEXT[view]}</span>
      {view !== "waiting" && (
        <Button variant={view === "manual" ? "primary" : "default"} onClick={onInsert} disabled={!canInsert}>
          {view === "confirm" ? "Вставить сейчас" : "Вставить ссылку"}
        </Button>
      )}
      <Button onClick={copy}>{copied ? "Скопировано" : "Копировать"}</Button>
      <button type="button" className="link-btn" onClick={onCancel}>Отменить</button>
    </div>
  );
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

/** Сеанс закрыли, чтобы освободить ресурсы (не больше трёх агентов сразу): можно продолжить. */
function EvictedNote({ canResume, onResume, onClose }: { canResume: boolean; onResume: () => void; onClose: () => void }) {
  return (
    <div className="agent__unsent" role="status" aria-label="Сессия закрыта">
      <div>Сессия была закрыта, чтобы освободить ресурсы: одновременно работают не больше трёх агентов.</div>
      <div className="agent__unsent-row">
        <Button variant="primary" onClick={onResume} disabled={!canResume}>Продолжить прошлую</Button>
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
  /** Есть ли где показать терминал: в приложении и с установленным агентом. */
  const withScreen = shell && !(assistant && providers.length === 0);
  /** Вставить ссылку негде: браузер или ни одного агента. */
  const unavailable = !withScreen;

  // Сеанс этой записи — вне вкладки: переживает её закрытие.
  const s = agentSession(id);
  const v = useSyncExternalStore(s.subscribe, s.snapshot);
  const { phase, code, error, ready } = v;

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

  const root = useRef<HTMLDivElement>(null);
  const screen = useRef<HTMLDivElement>(null);
  /** Вопрос перед «Остановить» или «Перезапустить», пока агент что-то выводит. */
  const [asking, setAsking] = useState<"stop" | "restart" | null>(null);

  // Терминал сеанса — в эту вкладку, пока она открыта; закрытая вкладка его не гасит.
  useEffect(() => {
    const el = screen.current;
    if (!withScreen || !el) return;
    return s.attach(el);
  }, [s, withScreen]);

  /** `resume` — «Продолжить прошлую»: последний разговор агента в папке встречи. */
  const start = useCallback(async (resume = false, which = provider) => {
    if (!which) return;
    // Запуск обновил файлы встречи — строка «Контекст» тоже.
    if (await s.start(which, resume)) loadContext();
  }, [s, provider, loadContext]);

  // Просьба «Спросить агента»: ссылка ждёт вставки в агента своей записи; одна и та же просьба — один раз.
  useEffect(() => {
    if (!insert || taken.has(insert)) return;
    taken.add(insert);
    if (!insert.text) return;
    s.request(insert.text);
    loadContext();
    onTaken?.();
    // Фокус — сразу во вкладку (терминал или сама вкладка): прежний элемент
    // (реплика) скрыт вместе с «Расшифровкой». После переключения вкладки.
    setTimeout(() => {
      if (s.term && s.shown()) s.term.focus();
      else root.current?.focus();
    }, 0);
  }, [insert, s, loadContext, onTaken]);

  // Вставить некуда — ссылка в уведомление (её можно скопировать).
  const pending = v.pending;
  useEffect(() => {
    if (!pending) return;
    if (unavailable) s.giveUp(shell ? "none" : "browser");
    else if (nothing) s.giveUp("nothing");
  }, [pending, s, unavailable, nothing, shell]);

  // Агент не запущен — запускаем сами (один раз на просьбу; упавший запуск — кнопкой).
  useEffect(() => {
    if (!pending || unavailable || !ready || !provider || !contextTried || nothing) return;
    if (s.autoStarted === pending.seq || (phase !== "idle" && phase !== "exited")) return;
    s.autoStarted = pending.seq;
    void start();
  });

  // «Остановить» и «Перезапустить»: агент только что что-то выводил (отвечает) — сначала спросить.
  const answering = () => Date.now() - s.lastData < STOP_CONFIRM_MS;
  const stop = () => {
    if (answering()) setAsking("stop");
    else s.stop();
  };
  const restart = () => {
    if (answering()) setAsking("restart");
    else void start();
  };

  // Правая кнопка: есть выделение — копировать, нет — вставить (как в терминале Windows).
  const onContextMenu = (e: MouseEvent) => {
    const t = s.term;
    if (!t) return;
    e.preventDefault();
    if (t.hasSelection()) {
      void navigator.clipboard?.writeText(t.getSelection()).catch(() => {});
      t.clearSelection();
    } else {
      void pasteInto(t);
    }
  };

  const unsentNote = v.unsent && (
    <UnsentNote text={v.unsent.text} reason={v.unsent.reason} onOpenSettings={onOpenSettings}
      onClose={() => s.dismissUnsent()} />
  );
  // Ссылка ждёт вставки: видно, чего ждём. «Копировать» и «Отменить» — всегда
  // («Отменить» не выбрасывает ссылку: она уходит в уведомление); «Вставить» —
  // при диалоге первого запуска («сейчас») и после срока.
  const waiting = pending && !unavailable && !nothing && (
    <PendingStrip text={pending.text} view={pending.view} canInsert={phase === "running"}
      onInsert={() => s.deliver()} onCancel={() => s.giveUp("cancelled")} />
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
  /** В папке встречи уже работал этот агент (метка резидента) — можно продолжить. */
  const hadSession = !!provider && !!context?.sessions?.includes(provider);
  const evicted = v.evicted && !active ? v.evicted : null;
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
            В analysis.json поле issues — задачи Jira, которые анализ нашёл в речи.
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
          <TipLine>
            Если агент уже работал с этой встречей, «Продолжить прошлую» возвращает к последнему разговору в её папке
            (Claude Code — <code>--resume</code> того же сеанса, Codex — <code>resume --last</code>); «Новая сессия» начинает
            разговор заново.
          </TipLine>
          <TipLine>Копировать — Ctrl+Shift+C, вставить — Ctrl+Shift+V или правой кнопкой мыши.</TipLine>
          <TipLine>
            Агент продолжает работать, когда вы переходите на другую вкладку, к другой встрече или в настройки; у
            встречи в списке тогда зелёная точка. Одновременно работают до трёх агентов (включая открытый): давно не
            открывавшийся и ничего не делающий закрывается сам, и его можно продолжить. Остановить агента — «Остановить»; все агенты
            останавливаются, когда вы закрываете окно приложения.
          </TipLine>
        </HelpTip>
        {active ? (
          <>
            <Button onClick={restart} disabled={!ready || !provider || phase === "starting"}>Перезапустить</Button>
            <Button onClick={stop} disabled={phase !== "running"}>Остановить</Button>
          </>
        ) : (
          <>
            <Button variant="primary" onClick={() => void start()} disabled={!ready || !provider || nothing}>
              {hadSession || evicted ? "Новая сессия" : "Запустить"}
            </Button>
            {hadSession && !evicted && (
              <Button onClick={() => void start(true)} disabled={!ready || nothing}>Продолжить прошлую</Button>
            )}
          </>
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
      {evicted && (
        <EvictedNote canResume={ready && !nothing && providers.some((p) => p.id === evicted.provider)}
          onResume={() => void start(true, evicted.provider)} onClose={() => s.dismissEvicted()} />
      )}
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
      {asking && (
        <ConfirmDialog title={asking === "stop" ? "Остановить агента?" : "Перезапустить агента?"}
          confirmLabel={asking === "stop" ? "Остановить" : "Перезапустить"}
          message="Агент только что что-то выводил — возможно, ещё отвечает. Ответ оборвётся."
          onCancel={() => setAsking(null)}
          onConfirm={() => {
            setAsking(null);
            if (asking === "stop") s.stop();
            else void start();
          }} />
      )}
    </div>
  );
}
