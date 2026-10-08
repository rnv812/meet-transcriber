/**
 * Метки условий под строкой поиска (обобщение прежних меток категорий): категории (запоминаются),
 * группы, участники, даты, «есть/нет», длительность, «в названии». У каждой своя «✕», при двух и
 * больше — «Сбросить». Те же метки ставит и снимает панель «Фильтры».
 */

import { CalendarRange, CircleCheck, CircleSlash, Clock, Folder, Type, UserRound, X, type LucideIcon } from "lucide-react";
import { chipColor, chipText, type Chip, type ChipKind, type QueryContext } from "../../lib/libraryQuery";
import { CategoryDot } from "../../ui/Category";
import { Icon } from "../../ui/Icon";
import { Tip } from "../../ui/Tip";

const ICONS: Partial<Record<ChipKind, LucideIcon>> = {
  person: UserRound, date: CalendarRange, has: CircleCheck, lacks: CircleSlash, longer: Clock, shorter: Clock, title: Type,
};
/** Для диктора: что это за метка. */
const KIND_NAME: Record<ChipKind, string> = {
  category: "Категория", group: "Группа", person: "Участник", date: "Дата", has: "Есть", lacks: "Нет",
  longer: "Длительность", shorter: "Длительность", title: "Название",
};

export function QueryChips({ chips, ctx, onRemove, onClear }: {
  chips: Chip[];
  ctx: Pick<QueryContext, "categories" | "groups">;
  onRemove: (chip: Chip) => void;
  onClear: () => void;
}) {
  if (!chips.length) return null;
  return (
    <div className="rec-list__filters" role="group" aria-label="Условия поиска">
      {chips.map((chip) => {
        const text = chipText(chip, ctx);
        const icon = ICONS[chip.kind];
        const color = chipColor(chip, ctx);
        return (
          <Tip key={`${chip.kind}:${chip.value}`} content={`${KIND_NAME[chip.kind]}: ${text}`}>
          <span className={`cat-filter__chip query-chip query-chip--${chip.kind}`}>
            {chip.kind === "category" ? <CategoryDot color={color} />
              : chip.kind === "group" ? (
                <span className="query-chip__icon" style={color ? { color } : undefined} aria-hidden="true">
                  <Icon as={Folder} size="sm" />
                </span>
              ) : icon ? <span className="query-chip__icon" aria-hidden="true"><Icon as={icon} size="sm" /></span> : null}
            <span className="cat-filter__chip-text">{text}</span>
            <button type="button" className="cat-filter__clear" aria-label={`Убрать «${text}» из фильтра`}
              onClick={() => onRemove(chip)}><Icon as={X} size="sm" /></button>
          </span>
          </Tip>
        );
      })}
      {chips.length >= 2 && (
        <button type="button" className="link cat-filter__reset" onClick={onClear}>Сбросить</button>
      )}
    </div>
  );
}
