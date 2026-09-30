import { useState } from "react";
import type { Endpoint } from "../../lib/api";
import { duration } from "../../lib/format";
import type { Person } from "../../lib/types";
import { Avatar } from "../../ui/Avatar";
import { EmptyState } from "../../ui/EmptyState";
import { PersonCard } from "./PersonCard";
import { plural } from "./plural";
import "./voices.css";

type Props = {
  endpoint: Endpoint;
  people: Person[];
  onChanged: () => void;
  onOpenRecording: (id: string) => void;
};

type Override = { has: boolean; v: number };

export function VoicesPane({ endpoint, people, onChanged, onOpenRecording }: Props) {
  const [selected, setSelected] = useState<string | null>(null);
  const [overrides, setOverrides] = useState<Record<string, Override>>({});
  const current = people.find((p) => p.name === selected) ?? null;
  const info = (p: Person) => {
    const o = overrides[p.name];
    return { has: o?.has ?? p.has_avatar, v: o?.v ?? 0 };
  };

  if (people.length === 0) {
    return <EmptyState title="Пока никого. Назовите спикеров в карточке записи — голоса запомнятся здесь." />;
  }

  return (
    <div className="voices">
      <section className="voices__main">
        <h2 className="voices__title">
          {people.length} {plural(people.length, "человек", "человека", "человек")} · узнаются автоматически
        </h2>
        <div className="voices__grid">
          {people.map((p) => {
            const a = info(p);
            return (
              <button
                key={p.name}
                type="button"
                className={`person${p.name === selected ? " person--on" : ""}`}
                onClick={() => setSelected(p.name)}
              >
                <Avatar name={p.name} color={p.color} hasAvatar={a.has} version={a.v} size={40} endpoint={endpoint} />
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
        <aside className="voices__card">
          <PersonCard
            key={current.name}
            endpoint={endpoint}
            person={current}
            others={people.filter((p) => p.name !== current.name)}
            hasAvatar={info(current).has}
            version={info(current).v}
            onAvatar={(has) => setOverrides((m) => ({ ...m, [current.name]: { has, v: Date.now() } }))}
            onRenamed={(to) => { setSelected(to); onChanged(); }}
            onRemoved={(next) => { setSelected(next); onChanged(); }}
            onOpenRecording={onOpenRecording}
          />
        </aside>
      )}
    </div>
  );
}
