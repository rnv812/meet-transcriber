/**
 * Над списком записей: кнопка-список групп (GroupsPicker — имя области, по
 * нажатию дерево групп). Открыта группа — рядом её меню («⋯») и «×» — назад ко
 * всем записям; заголовок «Проект Альфа · 12 встреч» — для диктора (глазам
 * его показывает кнопка), папка базы знаний группы (если задана) — строкой ниже.
 * `/groups` не ответил — вместо кнопки «Группы недоступны» с повтором. Пустая
 * группа — EmptyState (ниже).
 */

import { Ellipsis, Folder, RotateCw, TriangleAlert, X } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { meetingsText, NO_GROUP } from "../../lib/groups";
import { Button } from "../../ui/Button";
import { EmptyState } from "../../ui/EmptyState";
import { Icon } from "../../ui/Icon";
import { IconButton } from "../../ui/IconButton";
import { Tip } from "../../ui/Tip";
import { GroupMenu } from "./GroupMenu";
import { GroupsPicker } from "./GroupsPicker";
import type { GroupsUi } from "./useGroupsUi";
import "./groups.css";

export function GroupHeader({ ui }: { ui: GroupsUi }) {
  const [menu, setMenu] = useState(false);
  const more = useRef<HTMLButtonElement>(null);
  if (!ui.shown) {
    if (!ui.unavailable) return null;
    return (
      <p className="group-head__note" role="note">
        <Icon as={TriangleAlert} size="sm" className="group-head__note-icon" />
        <Tip content="Служба записи не ответила">
          <span className="group-head__note-text">Группы недоступны</span>
        </Tip>
        <IconButton icon={RotateCw} size="xs" label="Повторить" onClick={ui.retry} />
      </p>
    );
  }
  const scope = ui.scope;
  const named = scope !== null && ui.scopeName !== null;
  const kbFolder = named && scope !== NO_GROUP ? ui.groups.find((g) => g.id === scope)?.kb_folder ?? null : null;
  // Только для чтения меню — лишь у неизвестной группы («Убрать из встреч»).
  const editable = named && scope !== NO_GROUP && (!ui.readOnly || !ui.groups.some((g) => g.id === scope));
  const close = (focusBack = true) => { setMenu(false); if (focusBack) more.current?.focus(); };
  return (
    <div className="group-head">
      <GroupsPicker ui={ui} />
      {named && (
        <h2 className="sr-only">
          {ui.scopeName}{ui.scopeCount !== null && ` · ${meetingsText(ui.scopeCount)}`}
        </h2>
      )}
      {editable && (
        <IconButton ref={more} icon={Ellipsis} label={`Действия с группой «${ui.scopeName}»`}
          aria-haspopup="menu" aria-expanded={menu} onClick={() => (menu ? close() : setMenu(true))} />
      )}
      {named && <IconButton icon={X} label="Показать все записи" onClick={() => ui.setScope(null)} />}
      {kbFolder && (
        <Tip content="Папка базы знаний группы: на её встречах она в карте ассистента целиком">
        <span className="group-head__kb">
          <Icon as={Folder} size="sm" />
          <span className="sr-only">Папка базы знаний: </span>
          <span className="group-head__kb-path">{kbFolder}</span>
        </span>
        </Tip>
      )}
      {menu && scope && <GroupMenu ui={ui} id={scope} anchor={more} align="end" onClose={close} />}
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
      hint="Перетащите встречи на группу в списке групп над поиском или выберите «Переместить в группу» в меню встречи"
      action={all} />
  );
}
