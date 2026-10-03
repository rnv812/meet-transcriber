import { useCallback, useState } from "react";
import type { Endpoint } from "../../lib/api";
import { duration } from "../../lib/format";
import type { Job, Person } from "../../lib/types";
import { Avatar } from "../../ui/Avatar";
import { EmptyState } from "../../ui/EmptyState";
import { PaneResizer } from "../../ui/PaneResizer";
import { VoiceBaseTip } from "../settings/tips";
import { PersonCard } from "./PersonCard";
import type { OpenAt } from "./RefChips";
import { plural } from "./plural";
import "./voices.css";

/**
 * Панель человека — колонка справа от сетки: сетка перестраивается под
 * оставшуюся ширину, панель не наезжает на неё и не выходит за край окна
 * (сетке остаётся не меньше GRID_MIN). Ширина своя у карточки и у «Профиля».
 */
export const GRID_MIN = 200;
const CARD_PANE = { def: 360, min: 300, max: 640, reserve: GRID_MIN };
const PROFILE_PANE = { def: 680, min: 420, max: 1100, reserve: GRID_MIN };

type Props = {
  endpoint: Endpoint;
  people: Person[];
  avatarVersion: Record<string, number>;
  onAvatar: (name: string) => void;
  onChanged: () => void;
  onOpenRecording: (id: string) => void;
  /** Задачи резидента (профиль перечитывается, когда его задача кончилась). */
  jobs?: Job[];
  /** Открыть встречу на реплике (ссылка в профиле). */
  onOpenAt?: OpenAt;
  /** Текст профиля во вкладку «Агент» встречи (null — последней в библиотеке). */
  onAskAgent?: (recording: string | null, text: string) => void;
};

export function VoicesPane({
  endpoint, people, avatarVersion, onAvatar, onChanged, onOpenRecording, jobs, onOpenAt, onAskAgent,
}: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const current = people.find((p) => p.name === selected) ?? null;
  /** Открыт «Профиль»: панели человека нужно больше места. */
  const [wide, setWide] = useState(false);
  const onWide = useCallback((w: boolean) => setWide(w), []);

  if (people.length === 0) {
    return (
      <EmptyState title="База голосов пуста"
        hint="Назовите спикеров в карточке записи — их голоса сохранятся здесь и будут узнаваться автоматически." />
    );
  }

  return (
    <div className="voices">
      <section className="voices__main">
        <h2 className="voices__title">
          {people.length} {plural(people.length, "человек", "человека", "человек")} · узнаются автоматически
          <VoiceBaseTip />
        </h2>
        <div className="voices__grid">
          {people.map((p) => {
            return (
              <button
                key={p.name}
                type="button"
                className={`person${p.name === selected ? " person--on" : ""}`}
                onClick={() => setSelected(p.name)}
              >
                <Avatar name={p.name} color={p.color} hasAvatar={p.has_avatar} version={avatarVersion[p.name]} size={40} endpoint={endpoint} />
                <span className="person__name">{p.name}</span>
                <span className="person__meta">
                  {p.meetings} {plural(p.meetings, "встреча", "встречи", "встреч")} · {duration(p.seconds)} речи
                </span>
              </button>
            );
          })}
        </div>
      </section>
      {current && (
        <PaneResizer key={wide ? "profile" : "card"} cssVar="--person-w" panel="after"
          name={wide ? "voices-profile" : "voices-card"} spec={wide ? PROFILE_PANE : CARD_PANE}
          label={wide ? "Ширина профиля" : "Ширина карточки человека"} />
      )}
      {current && (
        <aside className={`voices__card${wide ? " voices__card--wide" : ""}`}>
          <PersonCard
            key={current.name}
            endpoint={endpoint}
            person={current}
            others={people.filter((p) => p.name !== current.name)}
            version={avatarVersion[current.name]}
            onAvatar={() => onAvatar(current.name)}
            onRenamed={(to) => { setSelected(to); onChanged(); }}
            onRemoved={(next) => { setSelected(next); onChanged(); }}
            onOpenRecording={onOpenRecording}
            jobs={jobs}
            onOpenAt={onOpenAt}
            onAskAgent={onAskAgent}
            onWide={onWide}
          />
        </aside>
      )}
    </div>
  );
}
