/**
 * Окно-приложение: библиотека записей слева, редактор транскрипта справа.
 *
 * Это центр конвейера Recordly-типа: остановил запись → расшифровал →
 * здесь расставил спикеров (с записью голосов в базу — «обучение клона»),
 * поправил текст, экспортировал. Логики нет — всё через control API резидента;
 * источник истины редактора — `transcript.json` (structured), Markdown остаётся
 * форматом экспорта.
 */

import { StrictMode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createRoot } from "react-dom/client";

import {
  NoResidentError,
  type Endpoint,
  type Segment,
  type Transcript,
  audioUrl,
  getRecording,
  getRecordings,
  getTranscript,
  nameSpeakers,
  saveTranscript,
  transcribeRecording,
} from "./api";
import type { Recording } from "./types";
import { useEndpoint } from "./useEndpoint";
import "./editor.css";

function hms(seconds: number): string {
  const t = Math.max(0, Math.floor(seconds));
  const h = Math.floor(t / 3600);
  const m = Math.floor((t % 3600) / 60);
  const s = t % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

function humanDate(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("ru-RU", {
    day: "2-digit", month: "2-digit", year: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

// --- библиотека -----------------------------------------------------------

function Library({
  items,
  activeId,
  onOpen,
  onRefresh,
}: {
  items: Recording[];
  activeId: string | null;
  onOpen: (id: string) => void;
  onRefresh: () => void;
}) {
  return (
    <aside className="lib">
      <div className="lib__head">
        <span className="lib__title">Записи</span>
        <button className="icon-btn" onClick={onRefresh} title="Обновить">↻</button>
      </div>
      <div className="lib__list">
        {items.length === 0 && <p className="muted lib__empty">Записей пока нет.</p>}
        {items.map((rec) => (
          <button
            key={rec.id}
            className={`lib__item ${rec.id === activeId ? "lib__item--on" : ""}`}
            onClick={() => onOpen(rec.id)}
          >
            <span className="lib__name">{rec.title || rec.id}</span>
            <span className="lib__meta">
              {humanDate(rec.started_at)}
              {rec.duration_s != null && ` · ${hms(rec.duration_s)}`}
            </span>
            <span className="lib__badges">
              <span className={`badge ${rec.has_transcript ? "badge--ok" : ""}`}>
                {rec.has_transcript ? "расшифровано" : "не расшифровано"}
              </span>
              {rec.has_voices && <span className="badge">голоса</span>}
            </span>
          </button>
        ))}
      </div>
    </aside>
  );
}

// --- редактор -------------------------------------------------------------

/** Уникальные спикеры транскрипта в порядке появления. */
function speakersOf(segments: Segment[]): string[] {
  const seen: string[] = [];
  for (const s of segments) {
    const name = s.speaker ?? "—";
    if (!seen.includes(name)) seen.push(name);
  }
  return seen;
}

// Стабильные цвета по спикеру: индекс появления → тон. Цвет — навигационная
// подсказка «кто говорит», а не украшение.
const SPEAKER_HUES = [210, 150, 32, 280, 0, 180, 96, 320];
function speakerColor(index: number): string {
  return `hsl(${SPEAKER_HUES[index % SPEAKER_HUES.length]} 60% 62%)`;
}

function Editor({
  endpoint,
  recordingId,
  onSaved,
  onLost,
}: {
  endpoint: Endpoint;
  recordingId: string;
  onSaved: () => void;
  onLost: () => void;
}) {
  const [transcript, setTranscript] = useState<Transcript | null>(null);
  const [recording, setRecording] = useState<Recording | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [track, setTrack] = useState<"sys" | "mic">("sys");
  const [renames, setRenames] = useState<Record<string, string>>({});
  const [playingAt, setPlayingAt] = useState(0);
  const audioRef = useRef<HTMLAudioElement>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const detail = await getRecording(endpoint, recordingId);
      setRecording(detail);
      setTranscript(detail.transcript ?? (await getTranscript(endpoint, recordingId)));
    } catch (cause) {
      setTranscript(null);
      // Резидент мог перезапуститься на новом порту — просим окно перечитать
      // адрес, тогда эффект перезагрузит редактор с живым endpoint.
      if (cause instanceof NoResidentError) onLost();
      else setError(String(cause));
    } finally {
      setLoading(false);
    }
  }, [endpoint, recordingId, onLost]);

  useEffect(() => {
    setRenames({});
    setDirty(false);
    setNotice(null);
    void load();
  }, [load]);

  const speakers = useMemo(
    () => (transcript ? speakersOf(transcript.segments) : []),
    [transcript],
  );

  const seekTo = (seconds: number) => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.currentTime = seconds;
    void audio.play().catch(() => undefined);
  };

  const editText = (index: number, text: string) => {
    setTranscript((current) => {
      if (!current) return current;
      const segments = current.segments.map((s, i) =>
        i === index ? { ...s, text } : s,
      );
      return { ...current, segments };
    });
    setDirty(true);
  };

  const save = async () => {
    if (!transcript) return;
    setSaving(true);
    setError(null);
    try {
      const result = await saveTranscript(endpoint, recordingId, transcript);
      if (result.ok) {
        setDirty(false);
        setNotice("Сохранено");
      } else {
        setError(result.error ?? "не сохранилось");
      }
    } catch (cause) {
      setError(String(cause));
    } finally {
      setSaving(false);
    }
  };

  const applyNames = async () => {
    const mapping = Object.fromEntries(
      Object.entries(renames)
        .map(([k, v]) => [k, v.trim()])
        .filter(([, v]) => v),
    );
    if (Object.keys(mapping).length === 0) return;
    setSaving(true);
    setError(null);
    try {
      const result = await nameSpeakers(endpoint, recordingId, mapping);
      const parts = [`переименовано реплик: ${result.renamed}`];
      if (result.enrolled.length) parts.push(`голоса в базу: ${result.enrolled.join(", ")}`);
      if (result.voices_error) parts.push(`(голоса: ${result.voices_error})`);
      setNotice(parts.join("; "));
      setRenames({});
      await load();
      onSaved();
    } catch (cause) {
      setError(String(cause));
    } finally {
      setSaving(false);
    }
  };

  if (loading) return <div className="ed"><p className="muted">Загружаю…</p></div>;
  if (error && !transcript) {
    return (
      <div className="ed">
        <p className="ed__error">{error}</p>
        <button className="btn" onClick={() => void load()}>Повторить</button>
      </div>
    );
  }
  if (!transcript) {
    return (
      <div className="ed">
        <p className="muted">
          Эта запись ещё не расшифрована.
        </p>
        <button
          className="btn btn--primary"
          onClick={() => transcribeRecording(endpoint, recordingId).then(onSaved)}
        >
          Расшифровать
        </button>
      </div>
    );
  }

  const speakerIndex = (name: string | null) =>
    speakers.indexOf(name ?? "—");

  return (
    <div className="ed">
      <header className="ed__head">
        <div>
          <h1>{transcript.title || recording?.id}</h1>
          <p className="ed__sub">
            {humanDate(recording?.started_at ?? null)}
            {recording?.duration_s != null && ` · ${hms(recording.duration_s)}`}
            {` · реплик: ${transcript.segments.length}`}
          </p>
        </div>
        <div className="ed__actions">
          {notice && <span className="notice">{notice}</span>}
          {dirty && <span className="dirty">есть несохранённое</span>}
          <button
            className="btn btn--primary"
            onClick={() => void save()}
            disabled={saving || !dirty}
          >
            {saving ? "Сохраняю…" : "Сохранить"}
          </button>
        </div>
      </header>

      {error && <p className="ed__error">{error}</p>}

      <div className="ed__body">
        <div className="ed__transcript">
          {transcript.segments.map((seg, index) => {
            const active =
              playingAt >= seg.start && playingAt < seg.end;
            return (
              <div
                key={index}
                className={`turn ${active ? "turn--active" : ""} ${
                  seg.uncertain ? "turn--overlap" : ""
                }`}
              >
                <button
                  className="turn__meta"
                  onClick={() => seekTo(seg.start)}
                  title="Слушать с этого места"
                >
                  <span className="turn__time num">{hms(seg.start)}</span>
                  <span
                    className="turn__speaker"
                    style={{ color: speakerColor(speakerIndex(seg.speaker)) }}
                  >
                    {seg.speaker ?? "—"}
                    {seg.uncertain && <span className="turn__flag"> нахлёст</span>}
                  </span>
                </button>
                <textarea
                  className="turn__text"
                  value={seg.text}
                  rows={1}
                  onChange={(e) => {
                    editText(index, e.target.value);
                    e.target.style.height = "auto";
                    e.target.style.height = `${e.target.scrollHeight}px`;
                  }}
                  ref={(el) => {
                    if (el) {
                      el.style.height = "auto";
                      el.style.height = `${el.scrollHeight}px`;
                    }
                  }}
                />
              </div>
            );
          })}
        </div>

        <aside className="ed__side">
          <section className="ed__player">
            <div className="ed__tracks">
              <button
                className={track === "sys" ? "chip chip--on" : "chip"}
                onClick={() => setTrack("sys")}
              >
                Собеседник
              </button>
              <button
                className={track === "mic" ? "chip chip--on" : "chip"}
                onClick={() => setTrack("mic")}
              >
                Вы
              </button>
            </div>
            <audio
              ref={audioRef}
              className="ed__audio"
              controls
              preload="metadata"
              src={audioUrl(endpoint, recordingId, track)}
              onTimeUpdate={(e) => setPlayingAt(e.currentTarget.currentTime)}
            />
            <p className="muted ed__hint">
              Записи двух дорожек раздельны: «Собеседник» — то, что слышно, «Вы» —
              ваш микрофон. Клик по реплике перематывает сюда.
            </p>
          </section>

          <section className="ed__speakers">
            <h2 className="eyebrow">Спикеры</h2>
            <p className="muted ed__hint">
              Назовите — имя проставится во всём транскрипте, а голос запомнится в
              базе, и на следующей встрече он узнается сам.
            </p>
            {speakers.map((name, i) => (
              <div className="spk" key={name}>
                <span className="spk__dot" style={{ background: speakerColor(i) }} />
                <span className="spk__from">{name}</span>
                <input
                  className="spk__to"
                  placeholder="имя"
                  value={renames[name] ?? ""}
                  onChange={(e) =>
                    setRenames((r) => ({ ...r, [name]: e.target.value }))
                  }
                />
              </div>
            ))}
            <button
              className="btn btn--primary"
              onClick={() => void applyNames()}
              disabled={
                saving || Object.values(renames).every((v) => !v.trim())
              }
            >
              Назвать и запомнить голоса
            </button>
          </section>
        </aside>
      </div>
    </div>
  );
}

// --- окно -----------------------------------------------------------------

function App() {
  const { endpoint, reresolve, error: epError, missing } = useEndpoint();
  const [items, setItems] = useState<Recording[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!endpoint) return;
    try {
      const list = await getRecordings(endpoint);
      setItems(list.items);
      setActiveId((current) => current ?? list.items[0]?.id ?? null);
      setError(null);
    } catch (cause) {
      if (cause instanceof NoResidentError) void reresolve();
      else setError(String(cause));
    }
  }, [endpoint, reresolve]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Открыть конкретную запись по событию из панели (кнопка после расшифровки).
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    (async () => {
      const w = window as unknown as { __TAURI_INTERNALS__?: unknown };
      if (!w.__TAURI_INTERNALS__) return;
      const { listen } = await import("@tauri-apps/api/event");
      unlisten = await listen<string>("open-recording", (event) => {
        if (event.payload) {
          setActiveId(event.payload);
          void refresh();
        }
      });
    })();
    return () => unlisten?.();
  }, [refresh]);

  const shown = error ?? epError;

  return (
    <div className="win">
      <Library
        items={items}
        activeId={activeId}
        onOpen={setActiveId}
        onRefresh={() => void refresh()}
      />
      <main className="win__main">
        {shown && <p className="ed__error ed__error--top">{shown}</p>}
        {endpoint && activeId ? (
          <Editor
            endpoint={endpoint}
            recordingId={activeId}
            onSaved={() => void refresh()}
            onLost={() => void reresolve()}
          />
        ) : (
          <div className="ed">
            <p className="muted">
              {missing
                ? "Дежурный не запущен: он отдаёт записи и расшифровки."
                : endpoint
                  ? "Выберите запись слева."
                  : "Ищу дежурного…"}
            </p>
          </div>
        )}
      </main>
    </div>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  );
}
