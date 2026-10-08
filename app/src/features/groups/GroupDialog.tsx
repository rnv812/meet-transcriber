/**
 * Окно названия группы: новая группа, переименовать, назвать неизвестную
 * (с тем же id — встречи останутся в ней). Название 1–60 символов, не «Все
 * записи», без повтора (без учёта регистра и «ё») — проверка та же, что у
 * резидента, и теми же словами; ответ резидента с ошибкой показывается здесь же.
 *
 * Цвет — палитра (одна остановка Tab, стрелки по цветам). Окно модальное:
 * Esc и «Отмена» закрывают, Tab не уходит из окна, Enter сохраняет.
 */

import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react";
import { createPortal } from "react-dom";
import { CATEGORY_PALETTE } from "../../lib/categories";
import { cleanGroupName, GROUP_NAME_MAX, groupNameError } from "../../lib/groups";
import type { GroupInfo } from "../../lib/types";
import { Button } from "../../ui/Button";
import type { GroupDialogState } from "./useGroupsUi";
import "./groups.css";

const FOCUSABLE = "button:not([disabled]), input:not([disabled]), [tabindex='0']";

/** Цвет новой группы: первый из палитры, которого ещё нет у групп. */
export function freeColor(groups: Pick<GroupInfo, "color">[]): string {
  const used = new Set(groups.map((g) => g.color.toLowerCase()));
  return (CATEGORY_PALETTE.find((p) => !used.has(p.color.toLowerCase())) ?? CATEGORY_PALETTE[0]!).color;
}

export function GroupDialog({ state, groups, onSubmit, onClose }: {
  state: GroupDialogState;
  groups: GroupInfo[];
  /** Сохранить: null — получилось (окно закроет вызывающий), иначе текст ошибки. */
  onSubmit: (name: string, color: string) => Promise<string | null>;
  onClose: () => void;
}) {
  const [name, setName] = useState(state.mode === "rename" ? state.name : "");
  const [color, setColor] = useState(state.mode === "rename" ? state.color : freeColor(groups));
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLInputElement>(null);
  const titleId = useId();
  const errorId = useId();
  const self = state.mode === "rename" ? state.id : undefined;
  const title = state.mode === "create" ? "Новая группа" : state.mode === "rename" ? "Переименовать группу" : "Назвать группу";

  // Фокус — в поле; после закрытия — туда, где был.
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    input.current?.focus();
    input.current?.select();
    return () => { if (before && document.contains(before)) before.focus(); };
  }, []);

  // Модальное: Esc закрывает, где бы ни был фокус; Tab ходит по кругу внутри окна.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape" && !e.defaultPrevented) {
        e.preventDefault();
        e.stopPropagation();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab" || !box.current) return;
      const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (!items.length) return;
      const first = items[0]!;
      const last = items[items.length - 1]!;
      const at = document.activeElement;
      const inside = at instanceof HTMLElement && box.current.contains(at);
      if (!inside || (e.shiftKey && at === first) || (!e.shiftKey && at === last)) {
        e.preventDefault();
        (e.shiftKey ? last : first).focus();
      }
    };
    document.addEventListener("keydown", onKey, true);
    return () => document.removeEventListener("keydown", onKey, true);
  }, []);

  const submit = async () => {
    if (busy) return;
    const problem = groupNameError(name, groups, self);
    if (problem) { setError(problem); input.current?.focus(); return; }
    setBusy(true);
    const answer = await onSubmit(cleanGroupName(name), color);
    setBusy(false);
    if (answer) { setError(answer); input.current?.focus(); }
  };

  const current = CATEGORY_PALETTE.find((p) => p.color.toLowerCase() === color.toLowerCase());
  const focused = current ?? CATEGORY_PALETTE[0]!;
  const onPaletteKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const all = [...e.currentTarget.querySelectorAll<HTMLButtonElement>("[role=radio]")];
    const at = all.indexOf(document.activeElement as HTMLButtonElement);
    const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[e.key];
    let next = -1;
    if (step) next = (at + step + all.length) % all.length;
    else if (e.key === "Home") next = 0;
    else if (e.key === "End") next = all.length - 1;
    if (next < 0) return;
    e.preventDefault();
    all[next]?.focus();
    setColor(CATEGORY_PALETTE[next]!.color);
  };

  return createPortal(
    <div className="backdrop backdrop--modal confirm-layer" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div ref={box} className="sheet confirm group-dialog" role="dialog" aria-modal="true" aria-labelledby={titleId}>
        <form onSubmit={(e) => { e.preventDefault(); void submit(); }}>
          <div className="confirm__title" id={titleId}>{title}</div>
          {state.mode === "name" && (
            <p className="confirm__text">Встречи этой группы останутся в ней — у группы появится имя.</p>
          )}
          <label className="group-dialog__label">
            Название
            <input ref={input} className="field field--md group-dialog__input" value={name} maxLength={GROUP_NAME_MAX}
              aria-invalid={error ? true : undefined} aria-describedby={error ? errorId : undefined}
              placeholder="Например, Проект Альфа"
              onChange={(e) => { setName(e.target.value); setError(null); }} />
          </label>
          {error && <div className="group-dialog__error" id={errorId} role="alert">{error}</div>}
          <div className="group-dialog__label" id={`${titleId}-color`}>Цвет</div>
          <div role="radiogroup" aria-labelledby={`${titleId}-color`} className="group-dialog__palette"
            onKeyDown={onPaletteKey}>
            {CATEGORY_PALETTE.map((p) => (
              <button key={p.color} type="button" role="radio" aria-checked={p === current} aria-label={p.name}
                className="group-dialog__color" style={{ background: p.color }}
                tabIndex={p === focused ? 0 : -1} onClick={() => setColor(p.color)} />
            ))}
          </div>
          <div className="confirm__actions">
            <Button onClick={onClose}>Отмена</Button>
            <Button type="submit" variant="primary" busy={busy}>
              {state.mode === "rename" ? "Сохранить" : state.mode === "name" ? "Назвать" : "Создать"}
            </Button>
          </div>
        </form>
      </div>
    </div>,
    document.body,
  );
}
