/**
 * Над списком, когда открыта группа: «Проект Альфа · 12 встреч», меню группы
 * («⋯») и «×» — назад ко всем записям. Пустая группа — EmptyState.
 */

import { Ellipsis, X } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { meetingsText, NO_GROUP } from "../../lib/groups";
import { Button } from "../../ui/Button";
import { CategoryDot } from "../../ui/Category";
import { EmptyState } from "../../ui/EmptyState";
import { IconButton } from "../../ui/IconButton";
import { GroupMenu } from "./GroupMenu";
import type { GroupsUi } from "./useGroupsUi";
import "./groups.css";

export function GroupHeader({ ui }: { ui: GroupsUi }) {
  const [menu, setMenu] = useState(false);
  const more = useRef<HTMLButtonElement>(null);
  if (!ui.shown || !ui.scope || ui.scopeName === null) return null;
  const scope = ui.scope;
  const color = scope === NO_GROUP ? null : ui.groups.find((g) => g.id === scope)?.color ?? null;
  // Только для чтения меню — лишь у неизвестной группы («Убрать из встреч»).
  const editable = scope !== NO_GROUP && (!ui.readOnly || !ui.groups.some((g) => g.id === scope));
  const close = (focusBack = true) => { setMenu(false); if (focusBack) more.current?.focus(); };
  return (
    <div className="group-head">
      <CategoryDot color={color} />
      <h2 className="group-head__title">
        <span className="group-head__name">{ui.scopeName}</span>
        {ui.scopeCount !== null && <span className="group-head__count muted"> · {meetingsText(ui.scopeCount)}</span>}
      </h2>
      {editable && (
        <IconButton ref={more} icon={Ellipsis} size="sm" label={`Действия с группой «${ui.scopeName}»`}
          aria-haspopup="menu" aria-expanded={menu} onClick={() => (menu ? close() : setMenu(true))} />
      )}
      <IconButton icon={X} size="sm" label="Показать все записи" onClick={() => ui.setScope(null)} />
      {menu && <GroupMenu ui={ui} id={scope} anchor={more} align="end" onClose={close} />}
    </div>
  );
}

/**
 * Пусто в области: с поиском — «Ничего не найдено в «X»» и поиск по всем
 * записям; без поиска — пустая группа (или все встречи уже в группах).
 * Области нет — null (у списка свои пустые состояния).
 */
export function groupEmptyState(ui: GroupsUi, q: string): ReactNode {
  if (!ui.shown || !ui.scope || ui.scopeName === null) return null;
  const all = <Button variant="link" onClick={() => ui.setScope(null)}>Показать все записи</Button>;
  if (q.trim()) {
    return <EmptyState title={`Ничего не найдено в «${ui.scopeName}»`} hint="Поиск идёт только по встречам этой группы"
      action={<Button variant="link" onClick={() => ui.setScope(null)}>Искать во всех записях</Button>} />;
  }
  if (ui.scope === NO_GROUP) return <EmptyState title="Все встречи уже в группах" action={all} />;
  return (
    <EmptyState title="В группе пока нет встреч"
      hint="Перетащите встречи на группу в левой панели или выберите «Переместить в группу» в меню встречи"
      action={all} />
  );
}
