import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type MouseEvent } from "react";
import { agentPrompt, type AgentRequest } from "../../lib/agentRef";
import {
  buildView, INSIGHT_LABEL, turnAt, usableAnalysis, type InsightView,
} from "../../lib/analysisView";
import {
  answerAnalysisOffer, ApiError, cancelJob, deleteRecording, exportRecording, getDiagnostics, getRecording, getSettings,
  kbExport, patchRecording, runAnalysis, setRecordingCategory, transcribe, type Endpoint,
} from "../../lib/api";
import { clock, errorText, meetingEndOf } from "../../lib/format";
import { jiraCard, JiraLinks, jiraLinker, type JiraLinker } from "../../lib/jira";
import { DEFAULT_PREFS, markupPrefs, type MarkupPrefs } from "../../lib/markupPrefs";
import { agentKillRecording, inTauri, openFolder, saveText } from "../../lib/shell";
import { keepTranscript } from "../../lib/sameTranscript";
import { mergeTurns, speakersOf, type Turn } from "../../lib/speakers";
import { activeJobOf, failedRetranscribe, failureAdvice, isLiveRecording, statusOf } from "../../lib/status";
import { speakerTones } from "../../lib/tones";
import type { Category, ChatUpdatedEvent, Job, KbExport, LiveHint, Recording, Segment, Snapshot, Transcript } from "../../lib/types";
import { KIND_LABEL } from "../../live/liveModel";
import { AgentMark } from "../../ui/AgentMark";
import { Button } from "../../ui/Button";
import { useConfirm } from "../../ui/ConfirmDialog";
import { EmptyState } from "../../ui/EmptyState";
import { Loading } from "../../ui/Loading";
import { Tip } from "../../ui/Tip";
import type { AgentInsert } from "./AgentTab";
import {
  AnalysisOffer, AnalysisStatus, reanalyzeBlocked, reanalyzeLabel, TitleSuggestPopover, useAnalysis, useTitleSuggest,
} from "./analysis";
import { noModelText, noProvider, useAssistant } from "./assistant";
import { modelChoices, modelReady, PROVIDER_LABELS } from "../../lib/llm";
import { useImprove } from "./improve";
import { AudioPlayer, type AudioPlayerHandle } from "./AudioPlayer";
import { Callout } from "./Callout";
import { CardActions } from "./CardActions";
import { CardTabs, type CardStage } from "./CardTabs";
import { CardHeader } from "./CardHeader";
import { LiveCard } from "./LiveCard";
import { RecordingNow } from "./RecordingNow";
import { Transcribing } from "./Transcribing";
import { RediarizeDialog, rediarizeJobOf } from "./RediarizeDialog";
import { SpeakersPanel } from "./speakers/SpeakersPanel";
import { TranscriptView, type FindRequest, type SeekRequest } from "./TranscriptView";
import { useTextFix } from "./TextFix";
import { useTurnEdit } from "./TurnEdit";
import type { PersonColor } from "./Turns";
import { asrNoteText, systemAudioText } from "./asrNote";
import { MicSplitNote } from "./micSplit";
import { PaneResizer } from "../../ui/PaneResizer";
import "./card.css";

type Loaded = Recording & { transcript: Transcript | null };

const NO_PEOPLE: PersonColor[] = [];
/** Текст до спикеров: в шапке чипов спикеров нет. */
const NO_NAMES: string[] = [];

/** Строка хода над текстом до спикеров — что сейчас происходит. */
function textNoteText(st: { job: Job | null }): string {
  const job = st.job;
  if (!job) return "Спикеры не определены: расшифровка прервалась";
  if (job.state === "done") return "Текст готов · спикеры определены, обновляю…";
  // В очереди или идёт заново, а новый текст ещё не готов: виден прежний, без спикеров.
  if (job.state === "queued") return "Расшифровка в очереди · текст пока без спикеров";
  if (!job.text_ready) return "Распознаю заново · прежний текст без спикеров";
  return "Текст готов · определяю спикеров…";
}
const NO_SEGMENTS: Segment[] = [];
/** Одна ссылка на «задач нет»: новая ссылка `jobs` для вкладок — это обновление списка. */
const NO_JOBS: Job[] = [];
/** `<data_dir>/logs` с разделителем, каким пишет путь сам резидент. */
function logsDir(dataDir: string): string {
  const sep = dataDir.includes("\\") ? "\\" : "/";
  return `${dataDir.replace(/[\\/]+$/, "")}${sep}logs`;
}
const norm = (p: string) => p.replace(/\\/g, "/").toLowerCase();
/** Сколько первых реплик главы уходит агенту со ссылкой на главу. */
const CHAPTER_REFS = 6;

/**
 * Панель «Спикеры встречи»: на широкой карточке (от 880 px) расшифровка видна
 * рядом и не уже 400 px; на узкой панель — поверх карточки, не шире её.
 */
const SPEAKERS_PANE = { def: 420, min: 320, max: 760, reserve: (room: number) => (room >= 880 ? 400 : 0) };

export function RecordingCard({
  id, endpoint, jobs = NO_JOBS, snapshot = null, people = NO_PEOPLE, avatarVersion, onDeleted, onChanged, onPeopleChanged,
  chatEvent = null,
  onOpenSettings, find, refreshKey = 0, categories, onSnapshot,
}: {
  id: string;
  endpoint: Endpoint;
  jobs?: Job[];
  /** Последнее событие `chat.updated` этой записи (вкладка «Ассистент»). */
  chatEvent?: ChatUpdatedEvent | null;
  snapshot?: Snapshot | null;
  people?: PersonColor[];
  avatarVersion?: Record<string, number>;
  onDeleted?: () => void;
  onChanged?: () => void;
  onPeopleChanged?: () => void;
  /** Перейти в настройки; `section` — раздел, например "assistant". */
  onOpenSettings?: (section: string) => void;
  /** Открыть с запросом в поиске по расшифровке (из поиска по записям). */
  find?: FindRequest | null;
  /** Растёт, когда запись изменили снаружи (переименовали в списке): перечитать. */
  refreshKey?: number;
  /** Категории встреч из настроек: метка под названием и меню выбора. */
  categories?: Category[];
  /** Ответ команды записи со страницы «Идёт запись» — новый снимок резидента. */
  onSnapshot?: (s: Snapshot) => void;
}) {
  const [rec, setRec] = useState<Loaded | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmNode, confirm] = useConfirm();
  const player = useRef<AudioPlayerHandle>(null);
  const cardEl = useRef<HTMLElement>(null);
  /** Панель «Спикеры»: открыта ли, к какой строке перейти; `mounted` — уже открывали (правки живут скрытыми). */
  const [panel, setPanel] = useState<{ open: boolean; mounted: boolean; focus: { label: string; n: number } | null }>(
    { open: false, mounted: false, focus: null });
  /** «Переразделить на спикеров…»: открыт ли диалог. */
  const [rediarizeOpen, setRediarizeOpen] = useState(false);
  /** «Показать все реплики» из панели: свой запрос к поиску по расшифровке. */
  const [ownFind, setOwnFind] = useState<FindRequest | null>(null);

  /** Папка для встреч в базе знаний (`export.meetings_dir`): нет — нет и кнопки «В базу знаний». */
  const [meetingsDir, setMeetingsDir] = useState<string | null>(null);
  /** Настройки прочитаны: до этого «В базу знаний» — неактивная заготовка на своём месте. */
  const [settingsRead, setSettingsRead] = useState(false);
  /** Как подписан владелец микрофона (настройка) — «Это я» в меню реплики. */
  const [owner, setOwner] = useState("Вы");
  /** Что из разметки встречи показывать («Расшифровка: подсветка и разметка», «Анализ встречи»). */
  const [prefs, setPrefs] = useState<MarkupPrefs>(DEFAULT_PREFS);
  /** Ссылки на задачи Jira (адрес и шаблон ключей из настроек); null — выключены. */
  const [jira, setJira] = useState<JiraLinker | null>(null);
  /** Ответ на предложение включить авто-анализ (`analysis.consent`): "pending" — ещё не спрашивали. */
  const [consent, setConsent] = useState<string>("");
  /** `recording.auto_transcribe` и `analysis.auto` — что будет после записи и после расшифровки. */
  const [after, setAfter] = useState({ transcribe: true, analysis: false });
  /** Куда выгружено нажатием «В базу знаний» (для этой записи) и что не перезаписано. */
  const [kbDone, setKbDone] = useState<KbExport | null>(null);
  /** Дорожка плеера не загрузилась: реплики не перематывают, внизу — «Аудио недоступно». */
  const [audioFailed, setAudioFailed] = useState(false);
  /** «Спросить агента»: последняя ссылка для поля ввода вкладки «Агент». */
  const [agentAsk, setAgentAsk] = useState<AgentInsert | null>(null);
  /** Человек перемотал плеер: прокрутить расшифровку к реплике, звучащей в этот момент. */
  const [seekTo, setSeekTo] = useState<SeekRequest | null>(null);
  /** Реплика, звучащая сейчас (отметка «сейчас играет»); обновляется только при её смене. */
  const [nowTurn, setNowTurn] = useState<number | null>(null);
  const current = useRef({ endpoint, id });
  current.current = { endpoint, id };
  /** Состояние задач записи (`jobSig`) сейчас — и то, при котором началось последнее удачное перечитывание:
   *  разные — карточка ещё не догнала задачи (конец расшифровки: спикеры уже записаны, а `rec` прежний). */
  const sigNow = useRef("");
  const [loadedSig, setLoadedSig] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    getSettings(endpoint).then((s) => {
      if (!live) return;
      const dir = (s.export as { meetings_dir?: unknown } | undefined)?.meetings_dir;
      setMeetingsDir(typeof dir === "string" && dir ? dir : null);
      const name = (s.recording as { speaker_name?: unknown } | undefined)?.speaker_name;
      if (typeof name === "string" && name.trim()) setOwner(name.trim());
      setPrefs(markupPrefs(s));
      setJira(jiraLinker(s));
      const answer = (s.analysis as { consent?: unknown } | undefined)?.consent;
      setConsent(typeof answer === "string" ? answer : "");
      const recording = s.recording as { auto_transcribe?: unknown } | undefined;
      const auto = (s.analysis as { auto?: unknown } | undefined)?.auto;
      setAfter({ transcribe: recording?.auto_transcribe !== false, analysis: auto === true });
    }).catch(() => {}).finally(() => { if (live) setSettingsRead(true); });
    return () => { live = false; };
  }, [endpoint]);

  const load = useCallback(async () => {
    const stale = () => current.current.id !== id || current.current.endpoint !== endpoint;
    const sig = sigNow.current;
    try {
      const data = await getRecording(endpoint, id);
      if (stale()) return;
      setLoadedSig(sig);
      // Та же расшифровка — тот же объект: открытые окна правки не сбрасываются.
      setRec((prev) => {
        const transcript = keepTranscript(prev?.id === data.id ? prev.transcript : null, data.transcript);
        return transcript === data.transcript ? data : { ...data, transcript };
      });
      setError(null);
      setMissing(false);
    } catch (e) {
      if (stale()) return;
      // 404 — запись удалили, а ссылка на неё осталась (уведомление, ?recording=).
      if (e instanceof ApiError && e.status === 404) setMissing(true);
      else setError(errorText(e));
    }
  }, [endpoint, id]);

  // Реплика перематывает общий плеер: он играет обе стороны звонка сразу,
  // поэтому дорожку по имени спикера выбирать не нужно.
  const play = useCallback((t: Turn) => player.current?.seek(t.start, true), []);
  const seeked = useCallback((t: number) => setSeekTo((r) => ({ t, n: (r?.n ?? 0) + 1 })), []);
  /** Время из итогов: плеер — на это место (без воспроизведения), лента — к реплике. */
  const goToTime = useCallback((t: number) => {
    player.current?.seek(t);
    seeked(t);
  }, [seeked]);
  const audioAvailable = useCallback((ok: boolean) => setAudioFailed(!ok), []);

  // Состояние задач этой записи: при смене (очередь, готово) карточку надо перечитать.
  const jobSig = useMemo(
    // `text_ready` — текст уже записан, идут спикеры (Р4): показать его, не дожидаясь конца задачи.
    () => jobs.filter((j) => rec && norm(j.folder) === norm(rec.path))
      .map((j) => `${j.id}:${j.state}${j.text_ready ? ":text" : ""}`).join(","),
    [jobs, rec],
  );
  sigNow.current = jobSig;

  useEffect(() => {
    setRec(null); setError(null); setMissing(false); setKbDone(null); setAudioFailed(false);
    setPanel({ open: false, mounted: false, focus: null }); setOwnFind(null); setRediarizeOpen(false);
    setAgentAsk(null); setNowTurn(null);
    void load();
  }, [load]);
  // Просьба из поиска по записям важнее прежней своей.
  useEffect(() => { setOwnFind(null); }, [find]);
  const lastRefresh = useRef(refreshKey);
  useEffect(() => {
    if (refreshKey !== lastRefresh.current) { lastRefresh.current = refreshKey; void load(); }
  }, [refreshKey, load]);
  const lastSig = useRef(jobSig);
  useEffect(() => {
    if (jobSig !== lastSig.current) { lastSig.current = jobSig; void load(); }
  }, [jobSig, load]);

  const segments = rec?.transcript?.segments;
  // Импорт не знает длительность заранее: по транскрипту она известна точно.
  const spokenUntil = useMemo(
    () => (segments?.length ? segments.reduce((m, x) => Math.max(m, x.end), 0) : null), [segments]);
  const turns = useMemo(() => mergeTurns(segments ?? []), [segments]);
  // Ссылки на задачи Jira: настройки + что резидент нашёл во встрече (по репликам карточки).
  const jiraLinks = useMemo(() => jiraCard(jira, rec?.jira, turns), [jira, rec?.jira, turns]);
  const turnsRef = useRef(turns);
  turnsRef.current = turns;
  /** Где плеер сейчас (последнее сообщение): реплики пересобраны — отметка «играет» по нему. */
  const playheadAt = useRef<number | null>(null);
  const playhead = useCallback((t: number) => {
    playheadAt.current = t;
    const i = turnAt(turnsRef.current, t);
    setNowTurn(i < 0 ? null : i); // тот же номер — React не перерисует
  }, []);
  // Пришли спикеры (или правка пересобрала реплики): номер звучащей реплики — в новом списке,
  // до показа кадра, чтобы отметка не мигнула на чужой реплике.
  useLayoutEffect(() => {
    const t = playheadAt.current;
    if (t === null) return;
    const i = turnAt(turns, t);
    setNowTurn(i < 0 ? null : i);
  }, [turns]);
  const speakers = useMemo(() => speakersOf(segments ?? []), [segments]);
  const openSpeakers = useCallback((label?: string) => setPanel((p) => ({
    open: true, mounted: true, focus: label ? { label, n: (p.focus?.n ?? 0) + 1 } : p.focus,
  })), []);
  const nameSpeaker = useCallback((label: string) => openSpeakers(label), [openSpeakers]);
  // «Показать» убранные повторы: панель «Спикеры» со списком раскрытым.
  const [removedAsk, setRemovedAsk] = useState(0);
  const showRemoved = useCallback(() => { setRemovedAsk((n) => n + 1); openSpeakers(); }, [openSpeakers]);
  const closeSpeakers = useCallback(() => setPanel((p) => ({ ...p, open: false })), []);
  const playPhrase = useCallback((start: number, until: number) => player.current?.seek(start, true, until), []);
  const showTurns = useCallback((label: string) => setOwnFind((f) => ({
    q: `спикер:"${label}"`, t: null, n: Math.max(f?.n ?? 0, find?.n ?? 0) + 1,
  })), [find]);
  const speakersChanged = useCallback(() => {
    void load(); onChanged?.(); onPeopleChanged?.();
  }, [load, onChanged, onPeopleChanged]);
  const shownFind = ownFind ?? find;
  // «Спросить агента» отовсюду: ссылка (очищенная, lib/agentRef) — во вкладку «Агент».
  const askAgent = useCallback((request: AgentRequest) => {
    const text = agentPrompt(request);
    if (text) setAgentAsk({ text });
  }, []);
  // Вкладка «Агент» забрала просьбу: сброс — заново открытая вкладка её не повторит.
  const agentTaken = useCallback(() => setAgentAsk(null), []);
  const askTurns = useCallback((which: number[], intent?: string) => {
    const refs = which.flatMap((i) => {
      const t = turns[i];
      return t && t.kind !== "break" ? [{ t: t.start, speaker: t.speaker, text: t.texts.join(" ") }] : [];
    });
    askAgent({ kind: "turns", refs, intent });
  }, [turns, askAgent]);
  const askHint = useCallback((h: LiveHint) => askAgent({
    kind: "hint", refs: [{ t: h.source_t, speaker: KIND_LABEL[h.kind] ?? null, text: h.text }],
  }), [askAgent]);
  // Цвет спикера — один на шапку, ленту и панель «Спикеры» (lib/tones): по порядку в шапке.
  const colors = useMemo(() => speakerTones(speakers, people), [speakers, people]);
  const textFix = useTextFix({
    endpoint, id, turns, segments: segments ?? NO_SEGMENTS, playable: !!rec && Object.keys(rec.tracks).length > 0
      && !audioFailed, head: rec?.edit_head, onPlay: playPhrase, onChanged: speakersChanged,
  });
  const turnEdit = useTurnEdit({
    endpoint, id, turns, segments: segments ?? NO_SEGMENTS, people, owner, avatarVersion,
    onOpenPanel: nameSpeaker, onChanged: speakersChanged, onFixWord: textFix.openWord, head: rec?.edit_head,
    onAskAgent: askTurns,
  });
  // Правый щелчок по тексту: выделены слова — «Исправить…», иначе «Разделить реплику здесь».
  const { onContextMenu: fixMenu } = textFix;
  const { onSplitAt: splitMenu } = turnEdit;
  const onTextMenu = useCallback((t: number, e: MouseEvent<HTMLElement>) => {
    if (!fixMenu(t, e)) splitMenu(t, e);
  }, [fixMenu, splitMenu]);
  // Анализ встречи: состояние, «Переанализировать», «Предложить название».
  const assistantInfo = useAssistant(endpoint);
  const analysis = useAnalysis(endpoint, id, rec?.path ?? null, jobs, rec);
  // ✦ «Улучшить расшифровку»: задача, окно со списком замен, итог с «Отменить».
  const improve = useImprove({
    endpoint, id, folder: rec?.path ?? null, jobs, version: rec, head: rec?.edit_head,
    noModel: noProvider(assistantInfo), noModelReason: noModelText(assistantInfo),
    models: modelChoices(assistantInfo), playable: !!rec && Object.keys(rec.tracks).length > 0 && !audioFailed,
    onPlay: playPhrase, onChanged: speakersChanged,
  });
  // Разметка по репликам — один раз на анализ (и на смену расшифровки или настроек).
  const segmentCount = segments?.length ?? 0;
  const analysisDoc = usableAnalysis(analysis.state, segmentCount);
  const view = useMemo(
    () => buildView(turns, analysisDoc, segmentCount, prefs.parts), [turns, analysisDoc, segmentCount, prefs.parts]);
  // В «Расшифровке» — только то, что включено в «Подсветке и разметке».
  const transcriptView = useMemo(() => view && {
    ...view,
    types: prefs.typeIcons ? view.types : null,
    key: prefs.keyBorder ? view.key : null,
    chapters: prefs.chapterHeads ? view.chapters : [],
    insights: prefs.insights ? view.insights : [],
  }, [view, prefs]);
  const turnRefs = useCallback((from: number, to: number) => {
    const refs = [];
    for (let i = from; i <= to; i++) {
      const t = turns[i];
      if (t && t.kind !== "break") refs.push({ t: t.start, speaker: t.speaker, text: t.texts.join(" ") });
    }
    return refs;
  }, [turns]);
  // ✦ у главы: название, время и первые реплики главы.
  const askChapter = useCallback((c: number) => {
    const ch = view?.chapters[c];
    if (!ch) return;
    askAgent({
      kind: "chapter", cap: CHAPTER_REFS,
      about: `Глава ${ch.n} «${ch.title}», ${clock(ch.start)}–${clock(ch.end)}`,
      refs: turnRefs(ch.turn, ch.lastTurn),
    });
  }, [view, turnRefs, askAgent]);
  // ✦ у наблюдения: вид, текст, «почему» и реплики, на которые оно ссылается.
  const askInsight = useCallback((x: InsightView) => {
    askAgent({
      kind: "insight",
      about: `${INSIGHT_LABEL[x.kind] ?? "Наблюдение"}: ${x.text}${x.why ? ` Почему: ${x.why}` : ""}`,
      refs: x.refs.flatMap((r) => turnRefs(r, r)),
    });
  }, [turnRefs, askAgent]);
  const titleApplied = useCallback((updated: Recording) => {
    setRec((cur) => (cur ? { ...cur, ...updated, transcript: cur.transcript } : cur));
    onChanged?.();
  }, [onChanged]);
  const titleSuggest = useTitleSuggest(endpoint, id, titleApplied);

  if (!rec) {
    if (missing) return <EmptyState title="Запись не найдена" hint="Возможно, её удалили. Выберите другую в списке." />;
    return error ? <div className="card__error" role="alert">{error}</div> : <Loading label="Загружаю запись…" />;
  }

  const status = statusOf(rec, jobs, snapshot, { reloading: jobSig !== loadedSig });
  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError(null);
    try { await fn(); } catch (e) { setError(errorText(e)); } finally { setBusy(false); }
  };

  const hasAudio = Object.keys(rec.tracks).length > 0;
  const rediarizing = rediarizeJobOf(rec.path, jobs) !== null;
  const playable = hasAudio && !audioFailed;

  const rename = (title: string | null) => act(async () => {
    const updated = await patchRecording(endpoint, id, { title });
    setRec((cur) => (cur ? { ...cur, ...updated, transcript: cur.transcript } : cur));
    onChanged?.();
  });
  const chooseCategory = (category: string | null) => act(async () => {
    const updated = await setRecordingCategory(endpoint, id, category);
    setRec((cur) => (cur ? { ...cur, ...updated, transcript: cur.transcript } : cur));
    onChanged?.();
  });
  const doTranscribe = () => act(async () => { await transcribe(endpoint, id); onChanged?.(); await load(); });
  const noModel = noProvider(assistantInfo);
  /** Повтор возможен: выбранной модели — если она доступна, иначе — модели по умолчанию. */
  const canRerun = (provider?: string) => (provider ? modelReady(modelChoices(assistantInfo), provider) : !noModel);
  /** `provider` — модель, выбранная для этого анализа; нет — модель по умолчанию. */
  const doReanalyze = (provider?: string) => act(async () => {
    await (provider ? runAnalysis(endpoint, id, provider) : runAnalysis(endpoint, id));
    await analysis.reload();
  });
  // Предложение — одно на всё приложение: ответили (здесь или в настройках) — больше не видно.
  const offerAnalysis = consent === "pending" && status.kind === "ready" && turns.length > 0
    && !!assistantInfo?.provider;
  const answerOffer = (answer: "granted" | "declined") => act(async () => {
    await answerAnalysisOffer(endpoint, id, answer);
    setConsent(answer);
  });
  const active = activeJobOf(rec, jobs);
  // Отмена теряет сделанное — сначала спросить (фокус на «Продолжить»).
  const doCancel = async () => {
    if (!active) return;
    const queued = active.state === "queued";
    // Текст уже записан (Р4): он останется, без спикеров.
    const textKept = rec.transcript_phase === "text";
    const ok = await confirm({
      title: queued ? "Убрать из очереди?" : "Отменить расшифровку?",
      message: textKept
        ? "Текст останется без спикеров. Расшифровку можно будет запустить заново."
        : queued
          ? "Запись не будет расшифрована, пока вы не запустите расшифровку снова."
          : "Сделанная часть работы будет потеряна. Расшифровку можно будет запустить заново.",
      confirmLabel: queued ? "Убрать из очереди" : "Отменить расшифровку",
      cancelLabel: queued ? "Оставить" : "Продолжить расшифровку",
    });
    if (!ok) return;
    await act(async () => {
      await cancelJob(endpoint, active.id);
      onChanged?.();
      await load();
    });
  };
  const openLogs = () => act(async () => {
    const diag = await getDiagnostics(endpoint, 1);
    const dir = (diag.paths as { data_dir?: unknown } | undefined)?.data_dir;
    if (typeof dir !== "string" || !dir) throw new Error("Папка данных службы записи неизвестна");
    await openFolder(logsDir(dir));
  });
  const logsButton = inTauri() ? <Button onClick={openLogs} disabled={busy}>Открыть журнал</Button> : null;
  const cancelButton = active ? (
    <Button onClick={() => void doCancel()} disabled={busy}>
      {active.state === "queued" ? "Убрать из очереди…" : "Отменить расшифровку…"}
    </Button>
  ) : null;
  const retranscribeFailed = status.kind === "ready" ? failedRetranscribe(rec, jobs) : null;
  // Колонка «Анализ» страницы «Расшифровывается»: кто и когда сделает анализ встречи.
  const provider = assistantInfo?.provider ? PROVIDER_LABELS[assistantInfo.provider] ?? assistantInfo.provider : null;
  // Пока сведения о модели не пришли (или резидент её ищет) — без «подключите модель».
  const analysisWhen = !after.analysis ? "по кнопке, после расшифровки"
    : provider ? `${provider}, после расшифровки`
      : noModel ? "после расшифровки, когда подключите модель" : "после расшифровки";
  /** Строка хода над текстом до спикеров: спокойно, без полосы — текст уже можно читать. */
  const textNote = (st: Extract<typeof status, { kind: "text" }>) => (
    <div className="card__textfirst" role="status" aria-label="Ход расшифровки">
      <span className="card__textfirst-text">{textNoteText(st)}</span>
      {/* Например, разделение на спикеров идёт на процессоре — и почему. */}
      {st.job?.state === "running" && st.job.warning && <span className="muted card__textfirst-note">{st.job.warning}</span>}
      {!st.job && st.error && (
        // Строка обрезается многоточием: целиком — в подсказке.
        <Tip content={st.error} describe={false}><span className="muted card__textfirst-note">{st.error}</span></Tip>
      )}
      {/* Задача только что кончилась — ни отмены, ни повтора: спикеры уже записаны. */}
      {!st.job ? <Button onClick={doTranscribe} disabled={busy}>Расшифровать заново</Button>
        : st.job.state === "done" ? null : cancelButton}
    </div>
  );
  const doDelete = () => act(async () => {
    // Плеер отпускает файл до запроса: резидент не удалит открытый playback.opus.
    player.current?.release();
    // Агент во вкладке «Агент» работает в папке записи — Windows не удалит её, пока он жив.
    await agentKillRecording(id);
    await deleteRecording(endpoint, id);
    onDeleted?.();
  });
  const doExport = (format: string) => act(async () => {
    const { filename, content } = await exportRecording(endpoint, id, format);
    await saveText(filename, content);
  });
  // Неудача остаётся и в meta.json записи (`kb_export.error`): перечитываем карточку в любом случае.
  const doKbExport = () => act(async () => {
    setKbDone(null);
    try {
      setKbDone(await kbExport(endpoint, id));
    } finally {
      await load();
    }
  });
  const kbError = rec.kb_export?.error;

  // Вкладки — на всех этапах: агент (вкладка «Агент») переживает переход «живой
  // режим → расшифровка → готово», и даже остановку ассистента посреди записи
  // (запись идёт дальше — «Запись · Агент», агент получает ленту, что успела).
  const live = status.kind === "recording" && !!snapshot?.live && isLiveRecording(rec, snapshot);
  const stage: CardStage = status.kind === "ready" ? "ready" : status.kind === "text" ? "text" : live ? "live"
    : status.kind === "recording" ? "recording" : "pending";
  let first;
  switch (status.kind) {
    case "ready":
      first = turns.length ? (
        <TranscriptView turns={turns} colors={colors} playable={playable} onPlay={play}
          onNameSpeaker={nameSpeaker} onSpeaker={turnEdit.onSpeaker} selected={turnEdit.selected}
          onSelect={turnEdit.onSelect} onRestrictSelection={turnEdit.restrict} onSplitAt={onTextMenu}
          onAskAgent={askTurns}
          toolbar={turnEdit.bar || textFix.bar || improve.bar
            ? <div className="tbars">{turnEdit.bar}{textFix.bar}{improve.bar && <div className="tfix-bar">{improve.bar}</div>}</div>
            : null}
          onFix={textFix.openFromBar} fixHint={textFix.hintId}
          tools={
            // Причина недоступности — подсказкой на самой кнопке: недоступная .btn наведение
            // получает (ui/button.css).
            <Tip content={improve.blocked
              ?? "Улучшить расшифровку: ИИ найдёт неверно распознанные термины и покажет список замен"}>
              <Button variant="aurora" size="md" flat aria-label="Улучшить расшифровку" disabled={!!improve.blocked}
                onClick={() => void improve.start()}>
                <AgentMark size={16} />Улучшить
              </Button>
            </Tip>
          }
          find={shownFind} view={transcriptView} onAskChapter={askChapter} onAskInsight={askInsight} seekTo={seekTo} nowTurn={nowTurn}
          // Разовое предложение анализа — только на «Расшифровке», над лентой: вкладки не сдвигаются.
          notice={offerAnalysis ? (
            <AnalysisOffer busy={busy} onAnswer={answerOffer}
              onOpenSettings={onOpenSettings ? () => onOpenSettings("analysis") : undefined} />
          ) : null} />
      ) : <EmptyState title="В записи нет речи" />;
      break;
    case "text":
      // Текст до спикеров (Р4): тот же список реплик, что у готовой записи (спикеры придут на
      // него же — место чтения и поиск не теряются), но без действий, которым нужны спикеры.
      first = turns.length ? (
        <TranscriptView turns={turns} colors={colors} playable={playable} onPlay={play} textPhase
          toolbar={textNote(status)} find={shownFind} seekTo={seekTo} nowTurn={nowTurn} />
      ) : (
        <EmptyState title="В записи нет речи" action={textNote(status)} />
      );
      break;
    case "untranscribed":
      first = <EmptyState title="Запись не расшифрована"
        action={<Button variant="primary" onClick={doTranscribe} disabled={busy}>Расшифровать</Button>} />;
      break;
    case "queued":
    case "running":
      // Карточка этапов из задачи; предупреждение задачи (процессор вместо видеокарты) — выноской.
      first = (
        <Transcribing job={active} queued={status.kind === "queued"}
          stage={status.kind === "running" ? status.stage : undefined}
          label={status.kind === "running" ? status.label : undefined}
          durationS={rec.duration_s ?? spokenUntil} tracks={Object.keys(rec.tracks).length}
          analysis={analysisWhen} actions={cancelButton} />
      );
      break;
    case "failed":
      first = (
        <div className="card__failed">
          <div className="card__error">{status.error || "Расшифровка не удалась"}</div>
          {failureAdvice(status.error) && <p className="card__advice">{failureAdvice(status.error)}</p>}
          <div className="card__row">
            <Button variant="primary" onClick={doTranscribe} disabled={busy}>Повторить</Button>
            {logsButton}
          </div>
        </div>
      );
      break;
    case "recording":
      first = live && snapshot?.live
        ? <LiveCard endpoint={endpoint} live={snapshot.live} snapshot={snapshot} onAskAgent={askHint} />
        : snapshot ? (
          <RecordingNow endpoint={endpoint} snapshot={snapshot} startedAt={rec.started_at}
            autoTranscribe={after.transcribe} noModel={noModel ? noModelText(assistantInfo) : null}
            onSnapshot={onSnapshot} />
        ) : <EmptyState title="Идёт запись…" />;
      break;
  }
  const body = (
    <CardTabs endpoint={endpoint} id={id} folder={rec.path} jobs={jobs} onOpenSettings={onOpenSettings}
      agentContext={`${rec.transcript_phase ?? "final"}:${rec.transcript_at ?? ""}`}
      showTranscript={shownFind?.n} stage={stage} transcript={first} agentRequest={agentAsk} chatEvent={chatEvent}
      onAskAgent={askAgent} onAgentTaken={agentTaken} meetingEnd={meetingEndOf(rec.started_at, rec.duration_s)}
      onTime={goToTime} />
  );

  const actions = (
    <CardActions
      canExport={status.kind === "ready"}
      canRetranscribe={status.kind === "ready" || (status.kind === "text" && !status.job)}
      busy={busy}
      onExport={doExport}
      onKbExport={meetingsDir && status.kind === "ready" ? doKbExport : undefined}
      kbPending={!settingsRead && status.kind === "ready"}
      onOpenFolder={() => void openFolder(rec.path)}
      onRetranscribe={doTranscribe}
      onRediarize={status.kind === "ready" && hasAudio ? () => setRediarizeOpen(true) : undefined}
      onReanalyze={status.kind === "ready" ? doReanalyze : undefined}
      reanalyzeBlocked={reanalyzeBlocked(analysis.state, noModel, noModelText(assistantInfo))}
      reanalyzePickBlocked={reanalyzeBlocked(analysis.state, false)}
      reanalyzeLabel={reanalyzeLabel(analysis.state)}
      onSuggestTitle={status.kind === "ready" ? titleSuggest.open : undefined}
      onImprove={status.kind === "ready" && turns.length ? (p) => void improve.start(p) : undefined}
      models={modelChoices(assistantInfo)}
      improveBlocked={improve.blocked}
      onDelete={doDelete}
    />
  );
  const asrNote = status.kind === "ready" ? asrNoteText(rec.asr_note) : null;
  const systemAudio = systemAudioText(rec.system_audio, rec.system_audio_reason);

  // Макет «карточка готовой встречи»: шапка (название и действия, строка о встрече),
  // под ней — тихие строки и выноски, вкладки и тело карточки с границей сверху.
  return (
    <section className={`rec-card${panel.open && status.kind === "ready" ? " rec-card--with-spk" : ""}`} ref={cardEl}>
      <CardHeader rec={rec} durationS={rec.duration_s ?? spokenUntil}
        speakers={status.kind === "text" ? NO_NAMES : speakers} people={people} tones={colors}
        endpoint={endpoint} avatarVersion={avatarVersion} onRename={rename} onNameSpeaker={nameSpeaker}
        onOpenSpeakers={status.kind === "ready" ? () => openSpeakers() : undefined} speakersOpen={panel.open}
        categories={categories} onCategory={chooseCategory}
        onOpenCategories={onOpenSettings ? () => onOpenSettings("categories") : undefined} actions={actions} />
      {titleSuggest.suggest && cardEl.current && (
        <TitleSuggestPopover anchor={cardEl.current.querySelector<HTMLElement>(".card__title") ?? cardEl.current}
          suggest={titleSuggest.suggest} onApply={(t) => void titleSuggest.apply(t)} onClose={titleSuggest.close} />
      )}
      <div className="card__notices">
        {error && <div className="card__error" role="alert">{error}</div>}
        {status.kind === "ready" && (
          <AnalysisStatus state={analysis.state} busy={busy} durationS={rec.duration_s ?? spokenUntil}
            onRun={canRerun(analysis.state?.state === "failed" ? analysis.state.provider : undefined)
              ? (p) => void doReanalyze(p) : undefined} />
        )}
        {status.kind === "ready" && improve.status}
        {kbDone && (
          <Callout tone="ok" label="Выгрузка в базу знаний"
            actions={inTauri() && <Button onClick={() => act(() => openFolder(kbDone.path))}>Открыть папку</Button>}>
            Выгружено: <code className="card__path">{kbDone.path}</code>
            {(kbDone.kept ?? []).map((name) => (
              <span key={name} className="card__kept">{name} изменён вручную — не перезаписан</span>
            ))}
            {(kbDone.notes ?? []).map((line) => (
              <span key={line} className="card__kept">{line}</span>
            ))}
          </Callout>
        )}
        {!kbDone && !error && kbError && (
          <Callout tone="err">Не удалось выгрузить в базу знаний: {kbError}</Callout>
        )}
        {retranscribeFailed && (
          <Callout tone="err" actions={<><Button onClick={doTranscribe} disabled={busy}>Повторить</Button>{logsButton}</>}>
            Перерасшифровка не удалась: {retranscribeFailed.error || "без подробностей"}
            <span className="muted"> · показана прежняя расшифровка</span>
          </Callout>
        )}
        {status.kind === "ready" && !rediarizeOpen && (rec.rediarize_ready || rediarizing) && (
          <Callout tone="note"
            actions={<Button onClick={() => setRediarizeOpen(true)}>{rediarizing ? "Подробнее" : "Посмотреть"}</Button>}>
            {rediarizing ? "Идёт переразделение на спикеров…" : "Новое разделение на спикеров готово — посмотрите и примените или откажитесь"}
          </Callout>
        )}
        {status.kind === "ready" && rec.diarization?.startsWith("skipped_") && (
          // Без токена HF (или без доступа к модели) расшифровка идёт одним потоком; токен — в разделе «Спикеры».
          <Callout tone="warn"
            actions={onOpenSettings && <Button onClick={() => onOpenSettings("speakers")}>Настроить</Button>}>
            Без разделения на спикеров — настройте Hugging Face
          </Callout>
        )}
        {asrNote && <Callout tone="note" role="note">{asrNote}</Callout>}
        {status.kind === "ready" && (
          <MicSplitNote info={rec.mic_split} diarization={rec.diarization} endpoint={endpoint}
            onOpenSettings={onOpenSettings}
            onShowRemoved={showRemoved} />
        )}
        {systemAudio && <Callout tone="warn" role="note">{systemAudio}</Callout>}
      </div>
      <JiraLinks.Provider value={jiraLinks}>{body}</JiraLinks.Provider>
      {status.kind === "ready" && turnEdit.menu}
      {status.kind === "ready" && textFix.node}
      {status.kind === "ready" && improve.dialog}
      {confirmNode}
      {status.kind === "ready" && rediarizeOpen && (
        <RediarizeDialog endpoint={endpoint} id={id} folder={rec.path} jobs={jobs} ready={!!rec.rediarize_ready}
          twoTrack={"sys" in rec.tracks && "mic" in rec.tracks} playable={playable} onPlay={playPhrase}
          onClose={() => setRediarizeOpen(false)} onApplied={speakersChanged} />
      )}
      {panel.open && status.kind === "ready" && (
        <PaneResizer name="speakers" cssVar="--spk-w" spec={SPEAKERS_PANE} panel="after"
          label="Ширина панели спикеров" className="spk-resize" />
      )}
      {panel.mounted && status.kind === "ready" && (
        <SpeakersPanel endpoint={endpoint} recordingId={id} people={people} tones={colors} avatarVersion={avatarVersion}
          open={panel.open} focus={panel.focus} version={rec.transcript} playable={playable} cardRef={cardEl} jobs={jobs}
          onClose={closeSpeakers} onPlay={playPhrase} onShowTurns={showTurns} onChanged={speakersChanged}
          removedAsk={removedAsk} />
      )}
      {status.kind !== "recording" && (hasAudio ? (
        <AudioPlayer key={id} ref={player} endpoint={endpoint} id={id} durationHint={rec.duration_s ?? spokenUntil}
          onAvailable={audioAvailable} turns={turns} chapters={view?.chapters} importance={view?.importance}
          curveMode={prefs.curve} barLabels={prefs.barLabels} people={people} avatarVersion={avatarVersion}
          onSeeked={seeked} onPlayhead={playhead} />
      ) : (
        <div className="player player--off" role="status"><span className="muted">Аудио недоступно</span></div>
      ))}
    </section>
  );
}
