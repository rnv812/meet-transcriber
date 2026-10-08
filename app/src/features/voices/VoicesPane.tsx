import { Ellipsis, Search } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { dismissProfilesRemoved, getProfilesRemoved, type Endpoint } from "../../lib/api";
import { duration } from "../../lib/format";
import { inTauri, openFolder } from "../../lib/shell";
import type { Person, ProfilesRemovedNotice } from "../../lib/types";
import { Avatar } from "../../ui/Avatar";
import { Button } from "../../ui/Button";
import { Icon } from "../../ui/Icon";
import { IconButton } from "../../ui/IconButton";
import { PaneResizer } from "../../ui/PaneResizer";
import { Truncate } from "../../ui/Truncate";
import { ItemMenu } from "../recordings/ItemMenu";
import { VoiceBaseTip } from "../settings/tips";
import { MyVoice } from "./MyVoice";
import { PersonCard, type PersonIntent } from "./PersonCard";
import { plural } from "./plural";
import "./voices.css";

/**
 * Панель человека — колонка справа от таблицы: таблица занимает оставшуюся
 * ширину (и прокручивается внутри), панель не наезжает на неё и не выходит за
 * край окна (таблице остаётся не меньше GRID_MIN).
 */
export const GRID_MIN = 200;
const CARD_PANE = { def: 360, min: 300, max: 640, reserve: GRID_MIN };

type Props = {
  endpoint: Endpoint;
  people: Person[];
  avatarVersion: Record<string, number>;
  onAvatar: (name: string) => void;
  onChanged: () => void;
  onOpenRecording: (id: string) => void;
  /** «Открыть настройки» у причины, по которой нельзя записать свой голос. */
  onOpenSettings?: (part: string) => void;
};

/**
 * Профили людей убраны в 0.3.2: резидент при обновлении перенёс заметки в один
 * файл и удалил остальное. Здесь — одна тихая строка об этом, до «Понятно».
 */
function ProfilesRemoved({ endpoint }: { endpoint: Endpoint }) {
  const [notice, setNotice] = useState<ProfilesRemovedNotice | null>(null);
  useEffect(() => {
    let live = true;
    getProfilesRemoved(endpoint).then((r) => live && setNotice(r.notice), () => {});
    return () => { live = false; };
  }, [endpoint]);
  if (!notice) return null;
  const done = () => {
    setNotice(null);
    void dismissProfilesRemoved(endpoint).catch(() => {});
  };
  return (
    <div className="voices__notice" role="status">
      <span>
        Профили людей убраны из Meet; сгенерированные профили удалены.
        {notice.notes ? ` Ваши заметки сохранены в ${notice.notes}` : ""}
      </span>
      <span className="voices__notice-actions">
        {notice.notes && inTauri() && (
          <Button onClick={() => openFolder(notice.folder).catch(() => {})}>Открыть папку</Button>
        )}
        <Button onClick={done}>Понятно</Button>
      </span>
    </div>
  );
}

/** Ищет ли строка поиска этого человека: по имени и по «Кто это», без регистра. */
export function personMatches(p: Person, needle: string): boolean {
  if (!needle) return true;
  return p.name.toLowerCase().includes(needle) || (p.role ?? "").toLowerCase().includes(needle);
}

export function VoicesPane({
  endpoint, people, avatarVersion, onAvatar, onChanged, onOpenRecording, onOpenSettings,
}: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [intent, setIntent] = useState<PersonIntent | undefined>(undefined);
  const [query, setQuery] = useState("");
  const [menu, setMenu] = useState<string | null>(null);
  const menuButton = useRef<HTMLElement | null>(null);
  const turn = useRef(0);
  const current = people.find((p) => p.name === selected) ?? null;

  /** Открыть карточку человека; `kind` — что в ней сразу сделать (нет — просто открыть). */
  const open = (name: string, kind?: PersonIntent["kind"]) => {
    setSelected(name);
    setIntent(kind ? { kind, n: ++turn.current } : undefined);
  };

  const needle = query.trim().toLowerCase();
  const shown = people.filter((p) => personMatches(p, needle));
  const empty = people.length === 0;

  return (
    <div className="voices">
      <section className="voices__main">
        {/* Одна колонка до 960 px по центру — и с людьми, и без них: шапка, «Мой голос», таблица. */}
        <div className="voices__col">
          <ProfilesRemoved endpoint={endpoint} />
          <header className="voices__head">
            <div className="voices__heading">
              <h1 className="voices__title">Голоса</h1>
              <p className="voices__sub">
                {empty ? "Пока никого"
                  : `${people.length} ${plural(people.length, "человек", "человека", "человек")} · узнаются автоматически`}
                <VoiceBaseTip />
              </p>
            </div>
            {!empty && (
              <div className="search voices__search">
                <Icon as={Search} />
                <input className="field field--md" type="search" placeholder="Найти человека"
                  aria-label="Найти человека" value={query} onChange={(e) => setQuery(e.target.value)} />
              </div>
            )}
          </header>
          <MyVoice endpoint={endpoint} onOpenSettings={onOpenSettings} />
          {empty ? (
            <div className="voices__empty">
              <b className="voices__empty-title">База голосов пуста</b>
              <p className="voices__empty-text">
                Назовите спикеров в карточке записи — их голоса сохранятся здесь и будут узнаваться автоматически.
              </p>
            </div>
          ) : shown.length === 0 ? (
            <p className="voices__none">Никого не нашлось — ни по имени, ни по «Кто это»</p>
          ) : (
            <div className="card voices__table">
              <div className="scroll-x">
                <table className="tbl" aria-label="Люди с голосом в базе">
                  <thead>
                    <tr>
                      <th scope="col"><span className="voices__th">Человек</span></th>
                      <th scope="col" className="voices__role-col"><span className="voices__th">Кто это</span></th>
                      <th scope="col"><span className="voices__th">Встреч</span></th>
                      <th scope="col"><span className="voices__th">Речи</span></th>
                      <th scope="col" className="w"><span className="sr-only">Действие</span></th>
                      <th scope="col" className="w"><span className="sr-only">Ещё</span></th>
                    </tr>
                  </thead>
                  <tbody>
                    {shown.map((p) => (
                      // Строка открывает карточку мышью; с клавиатуры — кнопка с именем (её нажатие
                      // всплывает сюда же), поэтому у самой кнопки своего обработчика нет.
                      <tr key={p.name} className="voices__row" aria-selected={p.name === selected}
                        onClick={() => open(p.name)}>
                        <td>
                          <button type="button" className="voices__person">
                            <Avatar name={p.name} color={p.color} hasAvatar={p.has_avatar}
                              version={avatarVersion[p.name]} size={32} endpoint={endpoint} />
                            <span className="voices__name">{p.name}</span>
                          </button>
                        </td>
                        {p.role ? (
                          <td className="voices__role"><Truncate>{p.role}</Truncate></td>
                        ) : (
                          <td className="voices__role voices__role--none" aria-label="не задано">—</td>
                        )}
                        <td>{p.meetings}</td>
                        <td className="voices__time">{duration(p.seconds)}</td>
                        <td className="w">
                          <Button size="xs" variant="ghost"
                            onClick={(e) => { e.stopPropagation(); open(p.name, "rename"); }}>Переименовать</Button>
                        </td>
                        <td className="w">
                          <IconButton icon={Ellipsis} size="xs" label={`Ещё: ${p.name}`}
                            onClick={(e) => {
                              e.stopPropagation();
                              menuButton.current = e.currentTarget;
                              setMenu(menu === p.name ? null : p.name);
                            }} />
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>
      </section>
      {menu && (
        <ItemMenu anchor={menuButton} align="end" label={`Действия с голосом «${menu}»`}
          items={[
            { label: "Открыть карточку", onSelect: () => { setMenu(null); open(menu); } },
            ...(people.length > 1
              ? [{ label: "Объединить с…", onSelect: () => { setMenu(null); open(menu, "merge"); } }] : []),
            { label: "Удалить голос", danger: true, separator: true,
              onSelect: () => { setMenu(null); open(menu, "delete"); } },
          ]}
          onClose={() => { setMenu(null); menuButton.current?.focus(); }} />
      )}
      {current && (
        <PaneResizer cssVar="--person-w" panel="after" name="voices-card" spec={CARD_PANE}
          label="Ширина карточки человека" />
      )}
      {current && (
        <aside className="voices__card">
          <PersonCard
            key={current.name}
            endpoint={endpoint}
            person={current}
            others={people.filter((p) => p.name !== current.name)}
            version={avatarVersion[current.name]}
            intent={intent}
            onAvatar={() => onAvatar(current.name)}
            onRenamed={(to) => { setSelected(to); onChanged(); }}
            onRemoved={(next) => { setSelected(next); onChanged(); }}
            onRoleSaved={onChanged}
            onOpenRecording={onOpenRecording}
          />
        </aside>
      )}
    </div>
  );
}
