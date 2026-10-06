/**
 * Группы в списке записей: «Переместить в группу ▸» для меню строки, кнопки
 * «В группу ▾» и «Убрать из группы» на панели выбора и что тащим, когда
 * строку (или все выбранные) перетаскивают на группу в левой панели.
 */

import { ChevronDown, FolderInput } from "lucide-react";
import { useMemo, useRef, useState, type RefObject } from "react";
import { dayLabel } from "../../lib/format";
import { meetingsText, UNKNOWN_NAME } from "../../lib/groups";
import type { GroupInfo, LibraryItem } from "../../lib/types";
import { Button } from "../../ui/Button";
import { Icon } from "../../ui/Icon";
import { ItemMenu } from "../recordings/ItemMenu";
import type { MeetingsPayload } from "./drag";
import { moveItems, type MoveToGroup } from "./moveMenu";
import type { GroupsUi } from "./useGroupsUi";

/** Выбранные записи и все записи списка — на момент действия (ref: меню строк мемоизированы). */
export type PickState = { chosen: string[]; items: Pick<LibraryItem, "id" | "group" | "title" | "started_at">[] };

/** Общая группа встреч: id, null — все без группы, undefined — разные (или встреч нет). */
export function sharedGroup(ids: string[], items: PickState["items"]): string | null | undefined {
  const by = new Map(items.map((r) => [r.id, r.group ?? null]));
  const groups = new Set(ids.map((id) => by.get(id) ?? null));
  return groups.size === 1 ? [...groups][0] : undefined;
}

const prevOf = (ids: string[], items: PickState["items"]) => {
  const by = new Map(items.map((r) => [r.id, r.group ?? null]));
  return Object.fromEntries(ids.map((id) => [id, by.get(id) ?? null]));
};

/** Перенос в группу для меню и панели выбора; групп нет (старый резидент) — undefined. */
export function useMoveToGroup(ui: GroupsUi | undefined, pick: RefObject<PickState>): MoveToGroup | undefined {
  const shown = ui?.shown ?? false;
  // Список групп меняется на каждое перечитывание (поиск, события): через ref — иначе меняются
  // действия строк и перерисовывается весь список.
  const list = useRef<GroupInfo[]>([]);
  list.current = ui?.groups ?? [];
  const readOnly = ui?.readOnly ?? false;
  const moveMeetings = ui?.moveMeetings;
  const create = ui?.create;
  return useMemo(() => {
    if (!shown || !moveMeetings || !create) return undefined;
    return {
      groups: () => list.current,
      targets: (id) => (pick.current.chosen.includes(id) ? pick.current.chosen : [id]),
      current: (ids) => sharedGroup(ids, pick.current.items),
      move: (ids, group) => void moveMeetings(ids, prevOf(ids, pick.current.items), group),
      create: readOnly ? undefined : (ids) => {
        const prev = prevOf(ids, pick.current.items);
        create((group, name) => void moveMeetings(ids, prev, group, name));
      },
    };
  }, [shown, readOnly, moveMeetings, create, pick]);
}

/**
 * Что тащим, если нажали на строку записи (`target` — где нажали): её саму или,
 * если она среди выбранных, все выбранные. Не строка (поиск, флажок, «⋯»,
 * фрагмент поиска, поле переименования) — null: перетаскивания нет.
 */
export function dragPayload(target: EventTarget | null, pick: PickState): MeetingsPayload | null {
  if (!(target instanceof Element)) return null;
  const main = target.closest(".rec-item__main");
  if (!main || main.classList.contains("rec-item__main--editing")) return null;
  const id = main.closest<HTMLElement>("[data-rec-id]")?.dataset.recId;
  if (!id) return null;
  const ids = pick.chosen.includes(id) ? pick.chosen : [id];
  const rec = pick.items.find((r) => r.id === id);
  const label = rec?.title ?? (rec?.started_at ? dayLabel(rec.started_at) : id);
  return { kind: "meetings", ids, prev: prevOf(ids, pick.items), label };
}

/** На панели выбора: «В группу ▾» (одиночный выбор) и «Убрать из группы», если группа у всех одна. */
export function GroupPickbar({ move, chosen }: { move: MoveToGroup; chosen: string[] }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const now = move.current(chosen);
  const close = (focusBack = true) => {
    setOpen(false);
    if (focusBack) button.current?.focus();
  };
  const name = typeof now === "string" ? move.groups().find((g) => g.id === now)?.name ?? UNKNOWN_NAME : null;
  return (
    <div className="rec-pickbar__row">
      <Button ref={button} icon={FolderInput} aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        В группу<Icon as={ChevronDown} size="sm" />
      </Button>
      {name !== null && (
        <Button title={`Убрать из «${name}»`} onClick={() => move.move(chosen, null)}>Убрать из группы</Button>
      )}
      {open && (
        <ItemMenu anchor={button} label={`Переместить в группу: ${meetingsText(chosen.length)}`}
          items={moveItems(move, chosen, close)} onClose={() => close()} />
      )}
    </div>
  );
}
