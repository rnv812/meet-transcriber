import { useEffect, useState } from "react";
import { useLibrary } from "../state/useLibrary";
import { usePeople } from "../state/usePeople";
import { useResident } from "../state/useResident";
import { RecordingsList } from "../features/recordings/RecordingsList";
import { VoicesPane } from "../features/voices/VoicesPane";
import { SettingsPane } from "../features/settings/SettingsPane";
import { RecordingCard } from "../features/card/RecordingCard";
import { initialRecording, onOpenRecording } from "../lib/shell";
import { EmptyState, OfflineState } from "../ui/EmptyState";
import { Nav, type Section } from "./Nav";
import { RecordingBadge } from "./RecordingBadge";

export function App() {
  const [section, setSection] = useState<Section>("recordings");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(() => initialRecording());
  const resident = useResident();
  const library = useLibrary(resident.endpoint ?? null, q, resident.libraryTick);
  const { people, refresh: refreshPeople, avatarVersion, bumpAvatar } = usePeople(resident.endpoint ?? null, resident.doneTick);
  const offline = resident.status === "offline";

  const openRecording = (id: string) => { setSelected(id); setSection("recordings"); };

  // В «Голоса» — со свежей базой: её меняют и расшифровки, и карточки записей.
  useEffect(() => {
    if (section === "voices") void refreshPeople();
  }, [section, refreshPeople]);

  // Клик по уведомлению: оболочка присылает id записи.
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let gone = false;
    onOpenRecording((id) => { setSelected(id); setSection("recordings"); })
      .then((off) => { if (gone) off(); else unlisten = off; })
      .catch((cause) => console.warn("open-recording:", cause));
    return () => { gone = true; unlisten?.(); };
  }, []);

  return (
    <div className="app">
      <Nav section={section} onSelect={setSection} />
      <div className="content">
        <header className="topbar">
          <RecordingBadge endpoint={resident.endpoint ?? null} snapshot={resident.snapshot ?? null}
            snapshotAt={resident.snapshotAt} online={resident.status === "online"}
            onSnapshot={resident.applySnapshot} />
        </header>
        <div className="panes">
          {section === "recordings" && (
            <div className="pane-list" data-pane="list">
              {offline ? <OfflineState /> : <RecordingsList
                selected={selected}
                onSelect={setSelected}
                library={library}
                resident={resident}
                q={q}
                onQ={setQ}
              />}
            </div>
          )}
          <main className="pane-detail" data-pane="detail">
            {offline ? (
              <OfflineState />
            ) : section === "voices" && resident.endpoint ? (
              <VoicesPane
                endpoint={resident.endpoint}
                people={people}
                avatarVersion={avatarVersion}
                onAvatar={(name) => { bumpAvatar(name); void refreshPeople(); }}
                onChanged={() => void refreshPeople()}
                onOpenRecording={openRecording}
              />
            ) : section === "settings" && resident.endpoint ? (
              <SettingsPane endpoint={resident.endpoint} recordingsDir={resident.snapshot?.recordings_dir ?? null} />
            ) : selected && resident.endpoint ? (
              <RecordingCard
                key={selected}
                id={selected}
                endpoint={resident.endpoint}
                jobs={library.jobs}
                snapshot={resident.snapshot ?? null}
                people={people}
                avatarVersion={avatarVersion}
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
