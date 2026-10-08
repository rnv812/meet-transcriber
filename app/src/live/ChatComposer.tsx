/**
 * Строка ввода чата с агентом-участником.
 *
 * - Enter — отправить, Shift+Enter — новая строка; пока идёт IME-набор,
 *   Enter не отправляет. Esc в пустом поле снимает фокус (клавиатура — звонку).
 * - Ctrl+V с картинкой — вложение (`pasteChatImage`). Если в буфере есть и
 *   текст (Excel, Word кладут рядом картинку ячеек) — вставляется только текст.
 * - Перетаскивание файлов — событием оболочки (`onFileDrop`: HTML5-перетаскивание
 *   на Windows перехватывает WebView2), только над зоной чата
 *   (`data-chat-drop`); путь уходит резиденту (`attachChatFile`) — он и
 *   проверяет его. «Приложить файл» (скрепка слева от поля) — диалог оболочки.
 * - Вложения видны здесь, с «×», до отправки; в ленту они попадают только с
 *   отправленным сообщением. «×» убирает вложение и у ассистента
 *   (`removeChatAttachment`): оно не дойдёт до агента, файл удаляется.
 * - «Стоп» — пока агент пишет ответ. Писать можно и тогда: сообщение встанет
 *   в очередь.
 * - Поле фокус само не берёт (панель поверх звонка), но после отправки
 *   остаётся в нём.
 * - Над пустым полем — быстрые вопросы (QUICK_QUESTIONS): щелчок отправляет.
 * - Вид — Atlas Aurora: рамка «Знак ИИ» (`aurora-edge`) на своей плотной
 *   поверхности (`--edge-bg`: `--surface-2`, в светлой — `--surface-1`), кнопки — `Button`.
 * - Текст и вложения живут в `useChat` (`chat.composer`): сворачивание панели и
 *   смена раскладки их не теряют.
 *
 * Устройство `.chat-compose` сверху вниз (точки расширения — здесь, а не снаружи):
 *   `.chat-compose__note` / `__reason` — ошибки и почему писать нельзя;
 *   `.chat-compose__quick` — быстрые вопросы над пустым полем;
 *   `.chat-slash` — подсказка слэш-команд (0.4, `slash.ts`): поле — «/» и имя без пробела →
 *     список `.menu` Aurora над полем (фильтр по набранному, описание и аргументы, навыки —
 *     с пометкой «навык»); дальше — дополнение аргументов: `/mcp ` — действие, `/mcp
 *     reconnect|enable|disable ` — MCP-серверы с состоянием, `/model ` — модели CLI (списки —
 *     от ассистента: `agent.mcp_servers`, `agent.models`); у прочих команд с аргументами —
 *     призрак `argumentHint` (`.chat-slash__ghost`). ↑ / ↓ — выбор, Tab — дописать, Enter —
 *     выбрать (команда без аргументов, сервер, модель — сразу отправить), Esc — убрать список.
 *     Выполняет команды ассистент;
 *   `.chat-compose__atts` — вложения до отправки (бейджи с миниатюрой и «×»);
 *   `.chat-compose__row` — скрепка · поле (`textarea`, вся клавиатура — `onKey`) · «Стоп» · «Отправить».
 * Снаружи строку держит док (`.chat-dock` рабочей области или вкладка
 * «Ассистент» карточки) — он задаёт только отступы и линию сверху.
 */

import { FileText, Image as ImageIcon, Paperclip, SendHorizontal, Square, X } from "lucide-react";
import { type ClipboardEvent, type KeyboardEvent, useEffect, useId, useLayoutEffect, useRef, useState } from "react";

import { errorText } from "../lib/format";
import { inTauri, onFileDrop, overChatDrop, pickChatFiles } from "../lib/shell";
import { BADGE_CLASS } from "../ui/badge";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
import { Tip } from "../ui/Tip";
import { type Suggestion, argHint, suggestions } from "./slash";
import type { Chat, ChatDraft } from "./useChat";
import "./chat.css";

/** Самое большее вложений в сообщении (как у резидента). */
export const MAX_ATTACHMENTS = 10;
const IMAGE_EXT = /\.(png|jpe?g|gif|webp|bmp|tiff?)$/i;
/** Высота поля — до шести строк, дальше прокрутка. */
const MAX_ROWS = 6;

type Draft = ChatDraft;
/** Быстрые вопросы над пустой строкой ввода: обычные сообщения агенту. */
export const QUICK_QUESTIONS = ["Что я пропустил?", "Что ответить?", "Кратко итоги"];

const baseName = (path: string) => path.split(/[\\/]/).filter(Boolean).pop() || path;
let draftSeq = 0;

export function ChatComposer({
  chat, disabledReason = null, vision = true, quick: questions = QUICK_QUESTIONS, placeholder = "Написать ассистенту…",
  autoFocus = false,
}: {
  chat: Chat;
  /** Почему писать нельзя (агент выключен, нет связи); null — можно. */
  disabledReason?: string | null;
  /** Модель видит картинки: иначе у картинки — пометка. */
  vision?: boolean;
  /** Быстрые вопросы над пустым полем (после встречи — свои). */
  quick?: string[];
  placeholder?: string;
  /** Взять фокус при появлении (после встречи — «Спросить ассистента»; панель поверх звонка — никогда). */
  autoFocus?: boolean;
}) {
  const { text, setText, drafts, setDrafts, dropped } = chat.composer;
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [slashActive, setSlashActive] = useState(0);
  /** Текст, при котором список команд убрали Esc (снова откроется, когда текст сменится). */
  const [slashOff, setSlashOff] = useState<string | null>(null);
  const menuRef = useRef<HTMLUListElement>(null);
  const menuId = useId();
  const field = useRef<HTMLTextAreaElement>(null);
  const disabled = disabledReason !== null;
  const chatRef = useRef(chat);
  chatRef.current = chat;

  const update = (key: string, patch: Partial<Draft>) =>
    setDrafts((cur) => cur.map((d) => (d.key === key ? { ...d, ...patch } : d)));

  const room = (cur: number) => {
    if (cur < MAX_ATTACHMENTS) return true;
    setError(`Не больше ${MAX_ATTACHMENTS} вложений в одном сообщении`);
    return false;
  };

  const track = (draft: Draft, upload: Promise<{ id: string; status: string; error?: string }>) => {
    setDrafts((cur) => [...cur, draft]);
    upload.then(
      (r) => dropped.has(draft.key) ? void chatRef.current.removeAttachment(r.id) : update(draft.key, r.status === "failed"
        ? { status: "failed", id: r.id, error: r.error || "не разобрано" }
        : { status: "ready", id: r.id }),
      (e) => update(draft.key, { status: "failed", error: errorText(e) }),
    );
  };

  const addImages = (files: File[]) => {
    setError(null);
    let n = drafts.length;
    for (const file of files) {
      if (!room(n++)) break;
      const preview = typeof URL.createObjectURL === "function" ? URL.createObjectURL(file) : undefined;
      const name = file.name && file.name !== "image.png" ? file.name : "Скриншот.png";
      track({ key: `d${++draftSeq}`, name, kind: "image", status: "uploading", preview }, chatRef.current.paste(file, name));
    }
  };

  const addPaths = (paths: string[]) => {
    setError(null);
    let n = drafts.length;
    for (const path of paths) {
      if (!room(n++)) break;
      const name = baseName(path);
      track({ key: `d${++draftSeq}`, name, kind: IMAGE_EXT.test(name) ? "image" : "doc", status: "uploading" },
        chatRef.current.attach(path));
    }
  };
  const addPathsRef = useRef(addPaths);
  addPathsRef.current = addPaths;

  useEffect(() => {
    if (autoFocus) field.current?.focus();
  }, [autoFocus]);

  const remove = (key: string) => {
    const gone = drafts.find((d) => d.key === key);
    if (gone?.preview) URL.revokeObjectURL?.(gone.preview);
    if (gone?.id) void chat.removeAttachment(gone.id);
    else if (gone?.status === "uploading") dropped.add(key);
    setDrafts((cur) => cur.filter((d) => d.key !== key));
    field.current?.focus();
  };

  // Перетаскивание: подписка одна, пока писать можно.
  useEffect(() => {
    if (disabled) return;
    let off: (() => void) | undefined;
    let dead = false;
    void onFileDrop((e) => {
      if (e.type === "leave") setOver(false);
      else if (e.type === "drop") {
        setOver(false);
        if (overChatDrop(e.x, e.y) && e.paths.length) addPathsRef.current(e.paths);
      } else setOver(overChatDrop(e.x, e.y));
    }).then((un) => { if (dead) un(); else off = un; }, (e) => console.warn("onFileDrop:", e));
    return () => { dead = true; off?.(); setOver(false); };
  }, [disabled]);

  // Высота поля — по тексту, до MAX_ROWS строк.
  useLayoutEffect(() => {
    const el = field.current;
    if (!el) return;
    el.style.height = "auto";
    const line = parseFloat(getComputedStyle(el).lineHeight) || 18;
    const pad = el.offsetHeight - el.clientHeight;
    const max = line * MAX_ROWS + 12;
    if (el.scrollHeight) el.style.height = `${Math.min(el.scrollHeight + pad, max)}px`;
  }, [text]);

  const uploading = drafts.some((d) => d.status === "uploading");
  const ready = drafts.filter((d) => d.status === "ready" && d.id).map((d) => d.id!);
  const canSend = !disabled && !uploading && (!!text.trim() || ready.length > 0);

  const submit = () => {
    if (!canSend) return;
    void chat.send(text, ready);
    for (const d of drafts) if (d.preview) URL.revokeObjectURL?.(d.preview);
    setText("");
    setDrafts(() => []);
    setError(null);
    field.current?.focus();
  };

  // Слэш-команды (0.4): «/» и имя → команды; `/mcp …`, `/model …` → аргументы (серверы, модели).
  const agent = chat.agent;
  const matches = disabled || drafts.length > 0 || slashOff === text ? []
    : suggestions(text, { commands: chat.commands, servers: agent?.mcp_servers, models: agent?.models });
  const menuOpen = matches.length > 0;
  const active = Math.min(slashActive, Math.max(matches.length - 1, 0));
  const ghost = !menuOpen && !disabled ? argHint(text, chat.commands) : null;
  const shape = matches.map((s) => s.key).join("|");
  useEffect(() => { setSlashActive(0); }, [shape]);
  useEffect(() => {
    if (menuOpen) menuRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView?.({ block: "nearest" });
  }, [active, menuOpen]);

  /**
   * Выбрать подсказку: Tab — только дописать; Enter — команда без аргументов, сервер или модель
   * уходят сразу (как и уже набранное целиком), остальное дописывается.
   */
  const choose = (s: Suggestion, complete = false) => {
    if (complete || (!s.send && s.text.trim() !== text.trim())) {
      setText(s.text);
      field.current?.focus();
      return;
    }
    void chat.send(s.text.trim());
    setText("");
    setError(null);
    field.current?.focus();
  };

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (menuOpen && !e.nativeEvent.isComposing) {
      const pick = matches[active]!;
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const step = e.key === "ArrowDown" ? 1 : -1;
        setSlashActive((active + step + matches.length) % matches.length);
        return;
      }
      if (e.key === "Tab" && !e.shiftKey) { e.preventDefault(); choose(pick, true); return; }
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); choose(pick); return; }
      if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setSlashOff(text); return; }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      // IME: Enter подтверждает набор, а не отправляет (keyCode 229 — старые WebView).
      if (e.nativeEvent.isComposing || e.keyCode === 229) return;
      e.preventDefault();
      submit();
    } else if (e.key === "Escape" && !text) {
      e.currentTarget.blur();
    }
  };

  const onPaste = (e: ClipboardEvent<HTMLTextAreaElement>) => {
    const files = Array.from(e.clipboardData?.files ?? []).filter((f) => f.type.startsWith("image/"));
    if (!files.length) return;
    // С текстом (Excel, Word, Outlook кладут рядом картинку) — только текст, как обычно.
    if (e.clipboardData.getData("text/plain") || e.clipboardData.getData("text/html")) return;
    e.preventDefault();
    addImages(files);
  };

  const pick = async () => {
    try {
      const paths = await pickChatFiles();
      if (paths.length) addPaths(paths);
    } catch (e) {
      setError(errorText(e));
    }
  };

  const ask = (question: string) => {
    if (disabled) return;
    void chat.send(question);
    field.current?.focus();
  };
  const quick = !disabled && !text && drafts.length === 0 && questions.length > 0;

  const sendTitle = uploading ? "Вложение ещё разбирается…" : "Отправить (Enter)";
  return (
    // Знак ИИ (aurora-edge): строка ввода к ассистенту.
    <div className={`chat-compose aurora-edge${over ? " is-over" : ""}${disabled ? " is-disabled" : ""}`}>
      {over && <div className="chat-compose__drop" aria-hidden="true">Отпустите, чтобы приложить</div>}
      {chat.note && <div className="chat-compose__note" role="alert">{chat.note}</div>}
      {error && <div className="chat-compose__note" role="alert">{error}</div>}
      {disabledReason && <div className="chat-compose__reason" role="status">{disabledReason}</div>}
      {quick && (
        <div className="chat-compose__quick" role="group" aria-label="Быстрые вопросы">
          {questions.map((q) => <Button key={q} onClick={() => ask(q)}>{q}</Button>)}
        </div>
      )}
      {drafts.length > 0 && (
        <ul className="chat-compose__atts" aria-label="Вложения">
          {drafts.map((d) => {
            const status = d.status === "uploading" ? (d.kind === "doc" ? "разбирается…" : "загружается…")
              : d.status === "failed" ? `не приложено: ${d.error}` : !vision && d.kind === "image" ? "модель не видит изображения" : null;
            return (
              <li key={d.key} className={`${BADGE_CLASS.plain} chat-draft chat-draft--${d.status}`} title={status ? `${d.name} — ${status}` : d.name}>
                {d.preview ? <img className="chat-draft__thumb" src={d.preview} alt={d.name} />
                  : <Icon as={d.kind === "image" ? ImageIcon : FileText} size="sm" />}
                <span className="chat-draft__name">{d.name}</span>
                {status && <span className="chat-draft__status">{status}</span>}
                <IconButton icon={X} size="xs" label={`Убрать вложение ${d.name}`} onClick={() => remove(d.key)} />
              </li>
            );
          })}
        </ul>
      )}
      {menuOpen && (
        // Подсказка команд (`.menu` Aurora) над полем: ↑ / ↓, Tab — дописать, Enter — выбрать, Esc — убрать.
        <ul ref={menuRef} id={menuId} className="menu open chat-slash" role="listbox"
          aria-label={text.startsWith("/") && !/\s/.test(text) ? "Команды" : "Варианты"}>
          {matches.map((s, i) => (
            <li key={s.key} id={`${menuId}-${i}`} data-index={i} role="option"
              aria-selected={i === active} className={i === active ? "active" : undefined}
              onMouseDown={(e) => e.preventDefault()} onMouseEnter={() => setSlashActive(i)}
              onClick={() => choose(s)}>
              <span className="chat-slash__name">{s.name}</span>
              {s.hint && <span className="chat-slash__hint">{s.hint}</span>}
              {s.tag && <span className={`${BADGE_CLASS.plain} chat-slash__tag`}>{s.tag}</span>}
              {s.desc && <small className={`chat-slash__desc${s.warn ? " is-warn" : ""}`}>{s.desc}</small>}
            </li>
          ))}
        </ul>
      )}
      {ghost && (
        // Призрак аргументов: что ждёт команда (`argumentHint` CLI, навыка или Meet).
        <div className="chat-slash__ghost" aria-hidden="true">{ghost}</div>
      )}
      <div className="chat-compose__row">
        {/* Скрепка — слева от поля (макет MeetLive). */}
        {inTauri() && (
          <IconButton icon={Paperclip} label="Приложить файл" disabled={disabled}
            tooltip="Приложить файл (или перетащите его сюда, или вставьте скриншот Ctrl+V)" onClick={() => void pick()} />
        )}
        <textarea ref={field} className="chat-compose__field" rows={1} value={text} disabled={disabled}
          aria-label="Сообщение ассистенту"
          aria-controls={menuOpen ? menuId : undefined} aria-expanded={menuOpen || undefined}
          aria-activedescendant={menuOpen ? `${menuId}-${active}` : undefined} aria-autocomplete="list"
          placeholder={disabled ? "Писать ассистенту сейчас нельзя" : placeholder}
          aria-description="Enter — отправить, Shift+Enter — новая строка, Ctrl+V — вставить скриншот, «/» — команды"
          onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onPaste={onPaste} />
        {chat.writing && (
          // Контурная, со словом «Стоп»: красная кнопка-значок в шапке останавливает запись, а не ответ.
          <Tip content="Остановить ответ ассистента">
            <Button size="sm" icon={Square} className="chat-compose__stop" aria-label="Остановить ответ"
              onClick={() => void chat.stop()}>Стоп</Button>
          </Tip>
        )}
        <IconButton icon={SendHorizontal} label="Отправить" variant="secondary" tooltip={sendTitle}
          disabled={!canSend} className="chat-compose__send" onClick={submit} />
      </div>
    </div>
  );
}
