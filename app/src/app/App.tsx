import { useState } from "react";
import { useLibrary } from "../state/useLibrary";
import { usePeople } from "../state/usePeople";
import { useResident } from "../state/useResident";
import { RecordingsList } from "../features/recordings/RecordingsList";
import { VoicesPane } from "../features/voices/VoicesPane";
import { RecordingCard } from "../features/card/RecordingCard";
import { EmptyState } from "../ui/EmptyState";
import { Nav, type Section } from "./Nav";
import { RecordingBadge } from "./RecordingBadge";

export function App() {
  const [section, setSection] = useState<Section>("recordings");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const resident = useResident();
  const library = useLibrary(resident.endpoint ?? null, q, resident.libraryTick);
  const { people, refresh: refreshPeople } = usePeople(resident.endpoint ?? null);
  const offline = resident.status === "offline";

  const openRecording = (id: string) => { setSelected(id); setSection("recordings"); };

  return (
    <div className="app">
      <Nav section={section} onSelect={setSection} />
      <div className="content">
        <header className="topbar">
          <RecordingBadge endpoint={resident.endpoint ?? null} snapshot={resident.snapshot ?? null} />
        </header>
        <div className="panes">
          {section === "recordings" && (
            <div className="pane-list" data-pane="list">
              <RecordingsList
                selected={selected}
                onSelect={setSelected}
                library={library}
                resident={resident}
                q={q}
                onQ={setQ}
              />
            </div>
          )}
          <main className="pane-detail" data-pane="detail">
            {offline ? (
              <EmptyState title="Сервис записи не запущен" hint="Окно переподключится само" />
            ) : section === "voices" && resident.endpoint ? (
              <VoicesPane
                endpoint={resident.endpoint}
                people={people}
                onChanged={() => void refreshPeople()}
                onOpenRecording={openRecording}
              />
            ) : selected && resident.endpoint ? (
              <RecordingCard
                key={selected}
                id={selected}
                endpoint={resident.endpoint}
                jobs={library.jobs}
                snapshot={resident.snapshot ?? null}
                people={people}
                onPeopleChanged={() => void refreshPeople()}
                onChanged={() => void library.refresh()}
                onDeleted={() => { setSelected(null); void library.refresh(); }}
              />
            ) : (
              <EmptyState title="Выберите запись" />
            )}
          </main>
        </div>
      </div>
    </div>
  );
}
