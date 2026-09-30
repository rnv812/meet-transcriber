import { useState } from "react";
import { useLibrary } from "../state/useLibrary";
import { useResident } from "../state/useResident";
import { EmptyState } from "../ui/EmptyState";
import { Nav, type Section } from "./Nav";

export function App() {
  const [section, setSection] = useState<Section>("recordings");
  const resident = useResident();
  const library = useLibrary(resident.endpoint, "", resident.libraryTick);
  const offline = resident.status === "offline";

  return (
    <div className="app">
      <Nav section={section} onSelect={setSection} />
      {section === "recordings" && (
        <div className="pane-list" data-pane="list">
          {library.items.length === 0 && <EmptyState title="Записей пока нет" />}
        </div>
      )}
      <main className="pane-detail" data-pane="detail">
        {offline ? (
          <EmptyState title="Сервис записи не запущен" hint="Окно переподключится само" />
        ) : (
          <EmptyState title="Выберите запись" />
        )}
      </main>
    </div>
  );
}
