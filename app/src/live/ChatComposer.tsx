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
 *   проверяет его. «📎» — диалог оболочки.
 * - Вложения видны здесь, с «×», до отправки; в ленту они попадают только с
 *   отправленным сообщением. «×» убирает вложение и у ассистента
 *   (`removeChatAttachment`): оно не дойдёт до агента, файл удаляется.
 * - «Стоп» — пока агент пишет ответ. Писать можно и тогда: сообщение встанет
 *   в очередь.
 * - Поле фокус само не берёт (панель поверх звонка), но после отправки
 *   остаётся в нём.
 * - Над пустым полем — быстрые вопросы (QUICK_QUESTIONS): щелчок отправляет.
 * - Текст и вложения живут в `useChat` (`chat.composer`): сворачивание панели и
 *   смена раскладки их не теряют.
 */

import { FileText, Image as ImageIcon, Paperclip, SendHorizontal, X } from "lucide-react";
import { type ClipboardEvent, type KeyboardEvent, useEffect, useLayoutEffect, useRef, useState } from "react";

import { errorText } from "../lib/format";
import { inTauri, onFileDrop, overChatDrop, pickChatFiles } from "../lib/shell";
import { Icon } from "../ui/Icon";
import { IconButton } from "../ui/IconButton";
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

export function ChatComposer({ chat, disabledReason = null, vision = true }: {
  chat: Chat;
  /** Почему писать нельзя (агент выключен, нет связи); null — можно. */
  disabledReason?: string | null;
  /** Модель видит картинки: иначе у картинки — пометка. */
  vision?: boolean;
}) {
  const { text, setText, drafts, setDrafts, dropped } = chat.composer;
  const [over, setOver] = useState(false);
  const [error, setError] = useState<string | null>(null);
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

  const onKey = (e: KeyboardEvent<HTMLTextAreaElement>) => {
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
  const quick = !disabled && !text && drafts.length === 0;

  const sendTitle = uploading ? "Вложение ещё разбирается…" : "Отправить (Enter)";
  return (
    <div className={`chat-compose${over ? " is-over" : ""}${disabled ? " is-disabled" : ""}`}>
      {over && <div className="chat-compose__drop" aria-hidden="true">Отпустите, чтобы приложить</div>}
      {chat.note && <div className="chat-compose__note" role="alert">{chat.note}</div>}
      {error && <div className="chat-compose__note" role="alert">{error}</div>}
      {disabledReason && <div className="chat-compose__reason" role="status">{disabledReason}</div>}
      {quick && (
        <div className="chat-compose__quick" role="group" aria-label="Быстрые вопросы">
          {QUICK_QUESTIONS.map((q) => (
            <button key={q} type="button" className="live-chip" onClick={() => ask(q)}>{q}</button>
          ))}
        </div>
      )}
      {drafts.length > 0 && (
        <ul className="chat-compose__atts" aria-label="Вложения">
          {drafts.map((d) => {
            const status = d.status === "uploading" ? (d.kind === "doc" ? "разбирается…" : "загружается…")
              : d.status === "failed" ? `не приложено: ${d.error}` : !vision && d.kind === "image" ? "модель не видит изображения" : null;
            return (
              <li key={d.key} className={`chat-draft chat-draft--${d.status}`} title={status ? `${d.name} — ${status}` : d.name}>
                {d.preview ? <img className="chat-draft__thumb" src={d.preview} alt={d.name} />
                  : <Icon as={d.kind === "image" ? ImageIcon : FileText} size="sm" />}
                <span className="chat-draft__name">{d.name}</span>
                {status && <span className="chat-draft__status">{status}</span>}
                <IconButton icon={X} size="sm" label={`Убрать вложение ${d.name}`} onClick={() => remove(d.key)} />
              </li>
            );
          })}
        </ul>
      )}
      <div className="chat-compose__row">
        <textarea ref={field} className="chat-compose__field" rows={1} value={text} disabled={disabled}
          aria-label="Сообщение ассистенту"
          placeholder={disabled ? "Писать ассистенту сейчас нельзя" : "Написать ассистенту…"}
          title="Enter — отправить, Shift+Enter — новая строка, Ctrl+V — вставить скриншот"
          onChange={(e) => setText(e.target.value)} onKeyDown={onKey} onPaste={onPaste} />
        {inTauri() && (
          <IconButton icon={Paperclip} label="Приложить файл" disabled={disabled}
            tooltip="Приложить файл (или перетащите его сюда, или вставьте скриншот Ctrl+V)" onClick={() => void pick()} />
        )}
        {chat.writing && (
          // Текстом, а не красным квадратом: тот в шапке останавливает запись.
          <button type="button" className="chat-compose__stop" aria-label="Остановить ответ"
            title="Остановить ответ ассистента" onClick={() => void chat.stop()}>Стоп</button>
        )}
        <IconButton icon={SendHorizontal} label="Отправить" variant="secondary" tooltip={sendTitle}
          disabled={!canSend} className="chat-compose__send" onClick={submit} />
      </div>
    </div>
  );
}
