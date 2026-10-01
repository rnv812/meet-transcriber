import { useEffect, useState } from "react";
import { useLibrary } from "../state/useLibrary";
import { usePeople } from "../state/usePeople";
import { useResident } from "../state/useResident";
import { RecordingsList } from "../features/recordings/RecordingsList";
import { VoicesPane } from "../features/voices/VoicesPane";
import { SettingsPane } from "../features/settings/SettingsPane";
import { RecordingCard } from "../features/card/RecordingCard";
import { initialRecording, initialSection, onOpenRecording, onOpenSection } from "../lib/shell";
import { EmptyState, OfflineState } from "../ui/EmptyState";
import { Button } from "../ui/Button";
import { Wizard } from "../features/wizard/Wizard";
import { useWizardGate } from "../features/wizard/useWizardGate";
import { Nav, type Section } from "./Nav";
import { RecordingBadge } from "./RecordingBadge";

export function App() {
  /** Раздел настроек, куда просили перейти: адрес `?section=`, событие оболочки, карточка. */
  const [settingsPart, setSettingsPart] = useState<{ part: string; n: number } | undefined>(() => {
    const part = initialSection();
    return part ? { part, n: 0 } : undefined;
  });
  const [section, setSection] = useState<Section>(() => (settingsPart ? "settings" : "recordings"));
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(() => initialRecording());
  const resident = useResident();
  const library = useLibrary(resident.endpoint ?? null, q, resident.libraryTick);
  const { people, refresh: refreshPeople, avatarVersion, bumpAvatar } = usePeople(resident.endpoint ?? null, resident.doneTick);
  const offline = resident.status === "offline";
  const gate = useWizardGate(resident.status, resident.endpoint ?? null);
  const recording = resident.snapshot?.status === "recording" || resident.snapshot?.live?.active === true;

  const openRecording = (id: string) => { setSelected(id); setSection("recordings"); };
  const select = (s: Section) => { setSettingsPart(undefined); setSection(s); };
  // Номер растёт с каждой просьбой: повторная возвращает в раздел, даже если он уже запрошен.
  const openSettings = (part: string) => {
    setSettingsPart((cur) => ({ part, n: (cur?.n ?? 0) + 1 }));
    setSection("settings");
  };

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

  // Оболочка просит раздел настроек (окно уже открыто — иначе пришло бы `?section=`).
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let gone = false;
    onOpenSection((part) => {
      setSettingsPart((cur) => ({ part, n: (cur?.n ?? 0) + 1 }));
      setSection("settings");
    })
      .then((off) => { if (gone) off(); else unlisten = off; })
      .catch((cause) => console.warn("open-section:", cause));
    return () => { gone = true; unlisten?.(); };
  }, []);

  if (gate.wizard) {
    return (
      <Wizard start={gate.wizard} engine={gate.engine} endpoint={resident.endpoint ?? null} recording={recording}
        onEngineChanged={() => void gate.refreshEngine()} onClose={gate.close} />
    );
  }

  // Без движка резидент не запустится: вместо «перезапускаю» — предложение поставить.
  const offlineList = gate.engineMissing ? <EmptyState title="Движок не установлен" /> : <OfflineState />;
  const offlineDetail = gate.engineMissing ? (
    <EmptyState title="Движок не установлен" hint="Без него приложение не записывает и не расшифровывает встречи."
      action={<Button variant="primary" onClick={() => gate.open("engine")}>Установить</Button>} />
  ) : <OfflineState />;

  return (
    <div className="app">
      <Nav section={section} onSelect={select} />
      <div className="content">
        <header className="topbar">
          <RecordingBadge endpoint={resident.endpoint ?? null} snapshot={resident.snapshot ?? null}
            snapshotAt={resident.snapshotAt} online={resident.status === "online"}
            onSnapshot={resident.applySnapshot} />
        </header>
        <div className="panes">
          {section === "recordings" && (
            <div className="pane-list" data-pane="list">
              {offline ? offlineList : <RecordingsList
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
              offlineDetail
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
              <SettingsPane endpoint={resident.endpoint} recordingsDir={resident.snapshot?.recordings_dir ?? null}
                initial={settingsPart?.part} initialTick={settingsPart?.n}
                onRunWizard={() => gate.open("hardware")} />
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
                onOpenSettings={openSettings}
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
