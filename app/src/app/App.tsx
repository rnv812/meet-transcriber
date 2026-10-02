import { useEffect, useMemo, useRef, useState } from "react";
import { useCategories } from "../state/useCategories";
import { useLibrary } from "../state/useLibrary";
import { usePeople } from "../state/usePeople";
import { useResident } from "../state/useResident";
import { RecordingsList } from "../features/recordings/RecordingsList";
import { VoicesPane } from "../features/voices/VoicesPane";
import { SettingsPane, type SettingsGuard } from "../features/settings/SettingsPane";
import { RecordingCard, type CardRequest } from "../features/card/RecordingCard";
import type { FindRequest } from "../features/card/TranscriptView";
import { loadCategoryFilter, NO_CATEGORY, saveCategoryFilter } from "../lib/categories";
import { searchable } from "../lib/search";
import {
  initialRecording, initialSection, onOpenRecording, onOpenSection, onSettingsCloseGuard, setSettingsDirty,
  settingsCloseAck, settingsCloseGo, settingsCloseStay,
} from "../lib/shell";
import { EmptyState, OfflineState } from "../ui/EmptyState";
import { Button } from "../ui/Button";
import { ConfirmDialog } from "../ui/ConfirmDialog";
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
  /** Запись открыта из поиска по записям: тот же запрос — в поиск по расшифровке. */
  const [find, setFind] = useState<FindRequest | null>(null);
  const findN = useRef(0);
  /** Запись изменили в списке (название, выгрузка): открытая карточка перечитывается. */
  const [cardTick, setCardTick] = useState(0);
  const resident = useResident();
  // Категории правят в настройках: из них вернулись — список перечитывается.
  const categories = useCategories(resident.endpoint ?? null, section === "settings");
  /** Фильтр списка по категориям (запоминается в этом окне); пусто — все записи. */
  const [catFilter, setCatFilter] = useState<string[]>(loadCategoryFilter);
  useEffect(() => saveCategoryFilter(catFilter), [catFilter]);
  // Удалённые из настроек категории в фильтре не участвуют — но только когда список уже пришёл.
  const activeFilter = useMemo(() => (categories.loaded
    ? catFilter.filter((k) => k === NO_CATEGORY || categories.list.some((c) => c.id === k)) : catFilter),
  [catFilter, categories.loaded, categories.list]);
  const library = useLibrary(resident.endpoint ?? null, q, resident.libraryTick, resident.contentTick, activeFilter);
  const { people, refresh: refreshPeople, avatarVersion, bumpAvatar } = usePeople(resident.endpoint ?? null, resident.doneTick);
  const offline = resident.status === "offline";
  const gate = useWizardGate(resident.status, resident.endpoint ?? null);
  const recording = resident.snapshot?.status === "recording" || resident.snapshot?.live?.active === true;

  /** Несохранённое в настройках: SettingsPane кладёт сюда список разделов и save. */
  const settingsGuard = useRef<SettingsGuard | null>(null);
  /** Уход из настроек ждёт ответа «Сохранить / Не сохранять / Остаться». */
  const [leaving, setLeaving] = useState<{ go: () => void; stay?: () => void } | null>(null);
  const sectionRef = useRef(section);
  sectionRef.current = section;
  /** Переход из настроек куда-то ещё — через вопрос, если там несохранённое. */
  const leaveSettings = (go: () => void) => {
    if (sectionRef.current === "settings" && settingsGuard.current?.dirty.length) setLeaving({ go });
    else go();
  };

  const openRecording = (id: string) => leaveSettings(() => { setSelected(id); setFind(null); setSection("recordings"); });
  /** Просьба к карточке из профиля человека: показать реплику или вставить текст агенту. */
  const [cardRequest, setCardRequest] = useState<CardRequest | null>(null);
  const openAt = (id: string, segment: number, t?: number, speaker?: string) => {
    openRecording(id);
    setCardRequest((r) => ({ n: (r?.n ?? 0) + 1, id, segment, t, speaker }));
  };
  // Нет общей встречи — последняя в библиотеке: агент всё равно получит текст.
  const askAgentIn = (id: string | null, text: string) => {
    const target = id ?? library.items[0]?.id ?? null;
    if (!target) return;
    openRecording(target);
    setCardRequest((r) => ({ n: (r?.n ?? 0) + 1, id: target, agent: text }));
  };
  const selectFromList = (id: string) => {
    setSelected(id);
    setFind(searchable(q) ? { q, t: null, n: ++findN.current } : null);
  };
  const openHit = (id: string, t: number) => {
    setSelected(id);
    setFind({ q, t, n: ++findN.current });
  };
  const select = (s: Section) => {
    if (s === "settings") { setSettingsPart(undefined); setSection(s); return; }
    leaveSettings(() => { setSettingsPart(undefined); setSection(s); });
  };
  // Номер растёт с каждой просьбой: повторная возвращает в раздел, даже если он уже запрошен.
  const openSettings = (part: string) => {
    setSettingsPart((cur) => ({ part, n: (cur?.n ?? 0) + 1 }));
    setSection("settings");
  };

  // Открытая запись изменилась в фоне (обрезка, выгрузка в базу знаний) — карточка перечитывается.
  const changed = resident.lastEvent?.kind === "recording.updated" ? resident.lastEvent : null;
  useEffect(() => {
    if (changed && changed.id === selected) setCardTick((n) => n + 1);
  }, [changed, selected]);

  // В «Голоса» — со свежей базой: её меняют и расшифровки, и карточки записей.
  useEffect(() => {
    if (section === "voices") void refreshPeople();
  }, [section, refreshPeople]);

  // Клик по уведомлению: оболочка присылает id записи (подписка одна — вопрос о настройках через ref).
  const leaveRef = useRef(leaveSettings);
  leaveRef.current = leaveSettings;

  // Крестик главного окна при несохранённых настройках: оболочка придержала
  // закрытие и спрашивает — тот же вопрос; ответ уходит обратно в оболочку.
  // Страница молчит (зависла) — оболочка закроет окно сама через 2 с.
  useEffect(() => {
    let off: (() => void) | null = null;
    let gone = false;
    onSettingsCloseGuard(() => {
      if (sectionRef.current !== "settings" || !settingsGuard.current?.dirty.length) {
        void settingsCloseGo();
        return;
      }
      void settingsCloseAck();
      setLeaving({ go: () => void settingsCloseGo(), stay: () => void settingsCloseStay() });
    })
      .then((stop) => { if (gone) stop(); else off = stop; })
      .catch((cause) => console.warn("settings-close-guard:", cause));
    return () => { gone = true; off?.(); };
  }, []);
  useEffect(() => {
    let unlisten: (() => void) | null = null;
    let gone = false;
    onOpenRecording((id) => leaveRef.current(() => { setSelected(id); setFind(null); setSection("recordings"); }))
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
        onRefreshEngine={gate.refreshEngine} onInstallStarted={gate.installStarted} onClose={gate.close} />
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
        {/* Полоса под системным заголовком тоже перетаскивает окно; кнопки в ней — нет (атрибут только у самой полосы). */}
        <header className="topbar" data-tauri-drag-region>
          <RecordingBadge endpoint={resident.endpoint ?? null} snapshot={resident.snapshot ?? null}
            snapshotAt={resident.snapshotAt} online={resident.status === "online"}
            onSnapshot={resident.applySnapshot} />
        </header>
        <div className="panes">
          {section === "recordings" && (
            <div className="pane-list" data-pane="list">
              {offline ? offlineList : <RecordingsList
                selected={selected}
                onSelect={selectFromList}
                onOpenHit={openHit}
                onChanged={(id) => { if (id === selected) setCardTick((n) => n + 1); }}
                onDeleting={(id) => { if (id === selected) setSelected(null); }}
                library={library}
                resident={resident}
                q={q}
                onQ={setQ}
                categories={categories.list}
                categoryFilter={activeFilter}
                onCategoryFilter={setCatFilter}
                onOpenSettings={openSettings}
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
                jobs={library.jobs}
                onOpenAt={openAt}
                onAskAgent={askAgentIn}
              />
            ) : section === "settings" && resident.endpoint ? (
              <SettingsPane endpoint={resident.endpoint} recordingsDir={resident.snapshot?.recordings_dir ?? null}
                initial={settingsPart?.part} initialTick={settingsPart?.n} guardRef={settingsGuard}
                onDirtyChange={(dirty) => void setSettingsDirty(dirty)}
                // Мастер заменяет окно целиком: несохранённое — через тот же вопрос.
                onRunWizard={(step) => leaveSettings(() => gate.open(step ?? "hardware"))} />
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
                find={find}
                request={cardRequest?.id === selected ? cardRequest : null}
                onRequestTaken={() => setCardRequest(null)}
                categories={categories.loaded ? categories.list : undefined}
                refreshKey={cardTick}
                onChanged={() => void library.refresh()}
                onDeleted={() => { setSelected(null); void library.refresh(); }}
              />
            ) : (
              <EmptyState title="Выберите запись" />
            )}
          </main>
        </div>
      </div>
      {leaving && (
        <LeaveSettings guard={settingsGuard.current} onStay={() => { leaving.stay?.(); setLeaving(null); }}
          onLeave={() => { const { go } = leaving; setLeaving(null); go(); }} />
      )}
    </div>
  );
}

/** «Сохранить / Не сохранять / Остаться» при уходе из настроек с несохранённым. */
function LeaveSettings({ guard, onStay, onLeave }: {
  guard: SettingsGuard | null; onStay: () => void; onLeave: () => void;
}) {
  const list = guard?.dirty ?? [];
  const where = `${list.length === 1 ? "разделе" : "разделах"} ${list.map((t) => `«${t}»`).join(", ")}`;
  const canSave = guard?.canSave ?? false;
  // «Остаться», пока идёт сохранение, — и после сохранения никуда не уходим.
  const open = useRef(true);
  useEffect(() => () => { open.current = false; }, []);
  return (
    <ConfirmDialog title="Сохранить изменения в настройках?" cancelLabel="Остаться" danger={false}
      message={canSave ? `Есть несохранённые изменения в ${where}.`
        : `В ${where} есть ошибки — сохранить не получится. Исправьте их или уйдите без сохранения.`}
      alt={{ label: "Не сохранять", danger: true, onClick: onLeave }}
      confirmLabel={canSave ? "Сохранить" : "Исправить"}
      onCancel={onStay}
      onConfirm={async () => {
        if (!canSave || !guard) { onStay(); return; }
        const saved = await guard.save();
        if (!open.current) return;
        if (saved) onLeave();
        else onStay();
      }} />
  );
}
