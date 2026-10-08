/**
 * Ход работы ассистента — как в Claude CLI (0.4, спец. §3): вызовы инструментов его хода
 * строками под его сообщением, по мере выполнения.
 *
 * Строка — одна моноширинная линия: состояние · вид («Bash», «Чтение», «Правка», «MCP»,
 * «Навык», «Веб») · суть (команда, путь, `сервер · инструмент`) с многоточием · «+N −M» у
 * правки · время. Состояние: выполняется — знак агента «пишет» (без движения при
 * `prefers-reduced-motion`), готово — ✓, ошибка — ✕ и первая строка ошибки под строкой,
 * отклонено — ⊘. Под строкой — решение ворот Meet, если вас спрашивали или Meet запретил
 * («спросил вас · разрешено», «запрещено: …»); обычное «разрешено автоматически» — в подсказке,
 * для диктора и в раскрытой строке (не шумит под каждым вызовом).
 *
 * Есть вывод или аргументы длиннее строки — строка сама кнопка (aria-expanded): раскрывает
 * блок кода с аргументами и первыми OUTPUT_LINES строками вывода, «Показать всё» — целиком
 * (в пределах 64 КБ, которые хранит журнал). Клавиатура — Enter/пробел на строке.
 *
 * Карточка согласия этого вызова (`tool_use_id`) — в самой строке: кнопки «Разрешить один
 * раз» / «Разрешать такое до конца встречи» / «Отклонить» (поток решения прежний).
 */

import { Ban, Check, ChevronRight, X } from "lucide-react";
import { useId, useRef, useState } from "react";

import { AgentMark } from "../ui/AgentMark";
import { Button } from "../ui/Button";
import { Icon } from "../ui/Icon";
import { Tip } from "../ui/Tip";
import { ConfirmCard } from "./ConfirmCard";
import type { ToolItem } from "./chatModel";
import type { Chat } from "./useChat";

/** Сколько строк вывода видно до «Показать всё». */
export const OUTPUT_LINES = 40;

/** Решения ворот «разрешено автоматически»: видны в раскрытой строке, подсказке и диктору. */
const QUIET_GATES = new Set(["auto", "allowed"]);

const STATUS_TEXT: Record<string, string> = {
  running: "выполняется", done: "готово", error: "ошибка", denied: "отклонено",
};

/** «35 мс», «1,2 с», «12 с», «2 мин 5 с». */
export function duration(ms: number | undefined): string {
  if (typeof ms !== "number" || !Number.isFinite(ms) || ms < 0) return "";
  if (ms < 1000) return `${Math.round(ms)} мс`;
  if (ms < 10_000) return `${(ms / 1000).toFixed(1).replace(".", ",")} с`;
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} с` : `${Math.floor(s / 60)} мин${s % 60 ? ` ${s % 60} с` : ""}`;
}

/** Первые `n` строк текста и сколько скрыто. */
export function headLines(text: string, n = OUTPUT_LINES): { head: string; hidden: number } {
  const lines = text.replace(/\n$/, "").split("\n");
  if (lines.length <= n) return { head: text.replace(/\n$/, ""), hidden: 0 };
  return { head: lines.slice(0, n).join("\n"), hidden: lines.length - n };
}

function StatusMark({ status }: { status: string }) {
  if (status === "running") return <AgentMark state="write" size={12} />;
  const icon = status === "done" ? Check : status === "denied" ? Ban : X;
  return <Icon as={icon} size="sm" className="chat-tool__icon" />;
}

function ToolRow({ row, card, chat, disabled }: ToolItem & { chat: Chat; disabled: boolean }) {
  const [open, setOpen] = useState(false);
  const [all, setAll] = useState(false);
  const id = useId();
  const status = typeof row.status === "string" && row.status in STATUS_TEXT ? row.status : "running";
  const label = String(row.label ?? row.name ?? "Инструмент");
  const summary = String(row.summary ?? "");
  const output = typeof row.output_preview === "string" ? row.output_preview : "";
  const input = typeof row.input_preview === "string" && row.input_preview.trim() !== summary.trim() ? row.input_preview : "";
  const expandable = !!(output.trim() || input.trim());
  const edit = typeof row.added === "number" || typeof row.removed === "number";
  const time = status === "running" ? "" : duration(row.duration_ms);
  const gate = row.gate && typeof row.gate === "object" ? row.gate.label : "";
  // «Разрешено автоматически» — обычный случай: в раскрытой строке и подсказке, не под каждой строкой.
  const quietGate = !!row.gate && typeof row.gate === "object" && QUIET_GATES.has(row.gate.decision);
  const cardOpen = !!card && chat.cards.some((c) => c.id === card.id);
  const shown = all ? { head: output, hidden: 0 } : headLines(output);
  // Суть целиком и решение ворот — подсказкой Aurora (строка обрезается; решение диктору — sr-only).
  const tip = [summary, gate].filter(Boolean).join(" — ");
  const line = (
    <>
      <span className="chat-tool__status" aria-hidden="true"><StatusMark status={status} /></span>
      <span className="sr-only">{STATUS_TEXT[status]}: </span>
      <span className="chat-tool__label">{label}</span>
      <span className="chat-tool__summary">{summary}</span>
      {edit && (
        <span className="chat-tool__diff">
          <span className="chat-tool__add">+{row.added ?? 0}</span> <span className="chat-tool__del">−{row.removed ?? 0}</span>
        </span>
      )}
      {time && <span className="chat-tool__time num">{time}</span>}
      {expandable && <Icon as={ChevronRight} size="sm" className="chat-tool__chevron" />}
    </>
  );
  return (
    <li className={`chat-tool chat-tool--${status}${open ? " is-open" : ""}`} data-tool={row.tool_use_id}>
      {expandable ? (
        <Tip content={tip} describe={false}>
          <button type="button" className="chat-tool__line" aria-expanded={open} aria-controls={`${id}-out`}
            onClick={() => setOpen(!open)}>
            {line}{quietGate && <span className="sr-only"> · {gate}</span>}
          </button>
        </Tip>
      ) : (
        <Tip content={tip} describe={false}>
          <div className="chat-tool__line">{line}{quietGate && <span className="sr-only"> · {gate}</span>}</div>
        </Tip>
      )}
      {status === "error" && typeof row.error === "string" && row.error && (
        <div className="chat-tool__error">{row.error}</div>
      )}
      {gate && (!quietGate || open) && <div className={`chat-tool__gate${status === "denied" ? " is-denied" : ""}`}>{gate}</div>}
      {card && (
        <div className="chat-tool__card">
          <ConfirmCard m={card} chat={chat} disabled={disabled} open={cardOpen} inline />
        </div>
      )}
      {expandable && open && (
        <div className="chat-tool__body" id={`${id}-out`}>
          {input && <pre className="chat-tool__code chat-tool__code--in" dir="ltr">{input}</pre>}
          {output && <pre className="chat-tool__code" dir="ltr">{shown.head}</pre>}
          {(shown.hidden > 0 || (all && row.truncated)) && (
            <div className="chat-tool__more">
              {shown.hidden > 0 && (
                <Button variant="link" onClick={() => setAll(true)}>Показать всё (ещё {shown.hidden} строк)</Button>
              )}
              {all && row.truncated && <span className="chat-tool__cut">Вывод обрезан: Meet хранит первые 64 КБ</span>}
            </div>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * Итог хода (0.5): «Выполнено 5 из 6 · не удалось: Bash npm run build — причина · ещё 1».
 * Только когда вызовов два и больше и ни один не выполняется; первая неудача — её
 * вид, суть и первая строка ошибки (или решение ворот у отклонённого).
 */
export function toolResult(items: ToolItem[]): { text: string; failed?: string } | null {
  const rows = items.map((it) => it.row);
  if (rows.length < 2 || rows.some((r) => r.status === "running" || !r.status)) return null;
  const done = rows.filter((r) => r.status === "done").length;
  const bad = rows.filter((r) => r.status === "error" || r.status === "denied");
  let text = `Выполнено ${done} из ${rows.length}`;
  const first = bad[0];
  if (first) {
    const what = [String(first.label ?? first.name ?? "Инструмент"), String(first.summary ?? "")].filter(Boolean).join(" ");
    const why = typeof first.error === "string" && first.error.trim()
      ? first.error.split("\n")[0]!.trim()
      : first.gate && typeof first.gate === "object" ? first.gate.label : "";
    text += ` · не удалось: ${what}${why ? ` — ${why}` : ""}${bad.length > 1 ? ` · ещё ${bad.length - 1}` : ""}`;
  }
  return { text, failed: typeof first?.tool_use_id === "string" ? first.tool_use_id : undefined };
}

/** Вызовы инструментов одного хода (под сообщением агента) и их итог. */
export function ToolRows({ items, chat, disabled }: { items: ToolItem[]; chat: Chat; disabled: boolean }) {
  const list = useRef<HTMLUListElement>(null);
  if (!items.length) return null;
  const result = toolResult(items);
  const toFailed = () => {
    const at = result?.failed ? list.current?.querySelector<HTMLElement>(`[data-tool="${CSS.escape(result.failed)}"]`) : null;
    at?.scrollIntoView?.({ block: "nearest" });
    at?.querySelector<HTMLElement>("button.chat-tool__line")?.focus({ preventScroll: true });
  };
  return (
    <>
      <ul ref={list} className="chat-tools" aria-label="Ход работы ассистента">
        {items.map((it) => <ToolRow key={it.row.id} {...it} chat={chat} disabled={disabled} />)}
      </ul>
      {result && (result.failed ? (
        <button type="button" className="chat-tools__result chat-tools__result--failed" onClick={toFailed}>
          {result.text}
        </button>
      ) : <div className="chat-tools__result">{result.text}</div>)}
    </>
  );
}
