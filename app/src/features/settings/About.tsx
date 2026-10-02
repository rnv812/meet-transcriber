import { useEffect, useState } from "react";
import pkg from "../../../package.json";
import { type Endpoint, getDiagnostics } from "../../lib/api";
import {
  checkUpdate, installUpdate, onUpdateProgress, openUrl, releasesPage,
  type UpdateCheck, type UpdateProgress,
} from "../../lib/shell";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { PathText, Row } from "./Section";
import { VoiceBaseTip } from "./tips";

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      window.setTimeout(() => setDone(false), 1500);
    } catch {
      /* буфер недоступен — адрес виден текстом */
    }
  };
  return <Button onClick={() => void copy()}>{done ? "Скопировано" : "Копировать"}</Button>;
}

const errorText = (cause: unknown) =>
  cause instanceof Error ? cause.message : String(cause);

/**
 * Ответ оболочки, когда идёт или ждёт расшифровка: не отказ, а вопрос
 * (тот же текст — `updater::WORK_IN_PROGRESS`). Прерванную задачу служба
 * записи новой версии поставит снова.
 */
export const UPDATE_CONFIRM_WORK =
  "Идёт расшифровка — она будет прервана и продолжится после обновления. Обновить сейчас?";

/** «12,5 МБ». */
export function megabytes(bytes: number): string {
  return `${(bytes / 1024 / 1024).toLocaleString("ru-RU", { maximumFractionDigits: 1 })} МБ`;
}

function UpdateTip() {
  return (
    <HelpTip label="Как работает обновление" title="Обновление">
      <TipLine>
        Приложение проверяет обновления только по этой кнопке — само в сеть за ними не ходит.
      </TipLine>
      <TipLine>
        «Скачать и установить» скачивает установщик новой версии с GitHub, сверяет его контрольную
        сумму и запускает. Приложение закроется, установщик предложит «Обновить до» новой версии и
        в конце запустит её. Записи, голоса и настройки сохранятся.
      </TipLine>
    </HelpTip>
  );
}

type State =
  | { kind: "idle" }
  | { kind: "checking" }
  | { kind: "checked"; result: UpdateCheck }
  | { kind: "failed"; error: string }
  | { kind: "installing"; result: UpdateCheck; progress: UpdateProgress | null }
  | { kind: "launched" }
  | { kind: "confirm"; result: UpdateCheck }
  | { kind: "install-failed"; result: UpdateCheck; error: string };

function UpdateRow() {
  const [state, setState] = useState<State>({ kind: "idle" });

  useEffect(() => {
    let stop: (() => void) | undefined;
    let gone = false;
    void onUpdateProgress((progress) =>
      setState((s) => (s.kind === "installing" ? { ...s, progress } : s)),
    ).then((unlisten) => {
      if (gone) unlisten();
      else stop = unlisten;
    });
    return () => {
      gone = true;
      stop?.();
    };
  }, []);

  const check = async () => {
    setState({ kind: "checking" });
    try {
      setState({ kind: "checked", result: await checkUpdate() });
    } catch (cause) {
      setState({ kind: "failed", error: errorText(cause) });
    }
  };

  const install = async (result: UpdateCheck, confirmed = false) => {
    setState({ kind: "installing", result, progress: null });
    try {
      await installUpdate(confirmed);
      setState({ kind: "launched" });
    } catch (cause) {
      const error = errorText(cause);
      setState(error === UPDATE_CONFIRM_WORK
        ? { kind: "confirm", result }
        : { kind: "install-failed", result, error });
    }
  };

  const busy = state.kind === "checking" || state.kind === "installing" || state.kind === "launched";
  const result = state.kind === "checked" || state.kind === "install-failed" ? state.result : null;
  const asking = state.kind === "confirm" ? state.result : null;
  const notes = result?.notes_url ?? null;

  return (
    <Row label="Обновления" hint="Проверка на GitHub — только по кнопке" help={<UpdateTip />}>
      <Button onClick={() => void check()} disabled={busy}>
        {state.kind === "checking" ? "Проверяю…" : "Проверить обновления"}
      </Button>
      <div className="update" role="status">
        {state.kind === "failed" && <span className="update__error">{state.error}</span>}
        {result && result.latest === null && (
          <span className="muted">Обновления пока не опубликованы</span>
        )}
        {result && result.latest !== null && !result.newer && (
          <span>У вас последняя версия ({result.current})</span>
        )}
        {result && result.latest !== null && result.newer && (
          <>
            <span>
              Доступна версия {result.latest}
              {result.size ? <span className="muted"> · {megabytes(result.size)}</span> : null}
            </span>
            {notes && <Button onClick={() => void openUrl(notes)}>Что нового</Button>}
            {result.asset_name ? (
              <Button variant="primary" onClick={() => void install(result)}>
                Скачать и установить
              </Button>
            ) : (
              <span className="muted">
                В выпуске нет установщика — скачайте его со страницы выпусков
              </span>
            )}
          </>
        )}
        {state.kind === "install-failed" && <span className="update__error">{state.error}</span>}
        {asking && (
          <>
            <span>{UPDATE_CONFIRM_WORK}</span>
            <Button variant="primary" onClick={() => void install(asking, true)}>Обновить сейчас</Button>
            <Button onClick={() => setState({ kind: "checked", result: asking })}>Отмена</Button>
          </>
        )}
        {state.kind === "installing" && <InstallProgress progress={state.progress} />}
        {state.kind === "launched" && <span>Установщик запущен, приложение закрывается…</span>}
      </div>
    </Row>
  );
}

function InstallProgress({ progress }: { progress: UpdateProgress | null }) {
  const total = progress?.total ?? 0;
  const done = progress?.done ?? 0;
  const pct = total > 0 ? Math.min(100, (done / total) * 100) : 0;
  return (
    <div className="update__progress">
      <span className="muted">
        {progress === null
          ? "Начинаю загрузку…"
          : total > 0
            ? `Скачиваю: ${megabytes(done)} из ${megabytes(total)}`
            : `Скачиваю: ${megabytes(done)}`}
      </span>
      <div className="update__track" aria-hidden="true">
        <div className="update__fill" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

/** Авторы и их роли — те же, что в NOTICE. */
export const AUTHORS: { name: string; role: string }[] = [
  { name: "Андрей Алейников", role: "автор проекта" },
  { name: "Андрей Сивуха (@ndrsvh)", role: "архитектура десктопного приложения" },
  { name: "Никита Резников", role: "десктопное приложение и интерфейс" },
];

export function About({ endpoint }: { endpoint: Endpoint }) {
  const [dataDir, setDataDir] = useState<string | null>(null);
  // Страница выпусков — от оболочки (репозиторий обновлений назван только там).
  const [releases, setReleases] = useState<string | null>(null);
  useEffect(() => {
    void releasesPage().then(setReleases);
  }, []);
  useEffect(() => {
    getDiagnostics(endpoint, 1)
      .then((d) => setDataDir((d.paths as Record<string, string> | undefined)?.data_dir ?? null))
      .catch(() => setDataDir(null));
  }, [endpoint]);
  return (
    <>
      <Row label="Версия"><span>{pkg.version}</span></Row>
      <UpdateRow />
      <Row
        label="Как обновиться"
        hint="Скачайте новый установщик и запустите его — данные сохранятся"
      >
        {releases && (
          <>
            <Button onClick={() => void openUrl(releases)}>Скачать новую версию</Button>
            <code className="path">{releases}</code>
            <CopyButton text={releases} />
          </>
        )}
      </Row>
      <Row label="Папка данных" hint="Настройки, журналы и база голосов" help={<VoiceBaseTip />}>
        {dataDir
          ? <span className="folder"><PathText path={dataDir} /><CopyButton text={dataDir} /></span>
          : <span className="muted">Неизвестно</span>}
      </Row>
      <Row label="Авторы" hint="Лицензия Apache-2.0" stack>
        <ul className="authors" aria-label="Авторы">
          {AUTHORS.map((a) => <li key={a.name}>{a.name} — {a.role}</li>)}
        </ul>
      </Row>
    </>
  );
}
