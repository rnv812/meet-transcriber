/**
 * Мастер первого запуска: железо → движок → Hugging Face → модели → запись →
 * готово. Шаги 1–2 работают без резидента (команды оболочки); шагам 3–5 он
 * нужен — после установки оболочка поднимает его сама, мастер ждёт до 90 с.
 *
 * «Пропустить мастер» есть на каждом шаге: сам он больше не откроется (флаг
 * пишет `onClose`), снова — из Настройки → Движок и модели.
 */

import { useEffect, useId, useState } from "react";
import { type Endpoint, getState, resolveEndpoint } from "../../lib/api";
import type { Profile } from "../../lib/estimate";
import { IS_MAC } from "../../lib/platform";
import { type EngineStatus, openLogs, residentStatus } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { StepDevices } from "./StepDevices";
import { StepDone } from "./StepDone";
import { type InstallPhase, StepEngine } from "./StepEngine";
import { StepHardware } from "./StepHardware";
import { StepHf } from "./StepHf";
import { StepModels } from "./StepModels";
import type { WizardStep } from "./useWizardGate";
import "../settings/settings.css";
import "./wizard.css";

const STEPS: { id: WizardStep; title: string }[] = [
  { id: "hardware", title: "Ваш компьютер" },
  { id: "engine", title: "Установка движка" },
  { id: "hf", title: "Hugging Face" },
  { id: "models", title: "Модели" },
  { id: "devices", title: "Запись" },
  { id: "done", title: "Готово" },
];
const NEEDS_RESIDENT: WizardStep[] = ["hf", "models", "devices"];

/** Ждём резидент после установки: раз в секунду, до 90 попыток. */
export const SERVICE_POLL_MS = 1000;
export const SERVICE_TRIES = 90;

/**
 * Адрес резидента для шагов 3–5. Окно (`fallback` — из useResident) находит
 * его само, но реже; здесь — чаще и с проверкой, что он отвечает: daemon.json
 * мог остаться от прошлого запуска. Оболочка говорит, что надзор сдался
 * (`resident_status` "failed"), — ждать дальше нечего: «crashed».
 */
function useService(fallback: Endpoint | null, active: boolean) {
  const [found, setFound] = useState<Endpoint | null>(null);
  const [failed, setFailed] = useState<false | "timeout" | "crashed">(false);
  const [round, setRound] = useState(0);
  const endpoint = fallback ?? found;

  useEffect(() => {
    if (!active || endpoint) return;
    let live = true;
    let tries = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    setFailed(false);
    const attempt = async () => {
      tries += 1;
      try {
        const next = await resolveEndpoint();
        await getState(next);
        if (live) setFound(next);
        return;
      } catch {
        /* ещё не поднялся */
      }
      const shell = await residentStatus().catch(() => null);
      if (!live) return;
      if (shell === "failed") setFailed("crashed");
      else if (tries >= SERVICE_TRIES) setFailed("timeout");
      else timer = setTimeout(() => void attempt(), SERVICE_POLL_MS);
    };
    void attempt();
    return () => {
      live = false;
      if (timer) clearTimeout(timer);
    };
  }, [active, endpoint, round]);

  return { endpoint, failed, retry: () => setRound((n) => n + 1) };
}

export function Wizard({
  start = "hardware", engine, endpoint, recording, onRefreshEngine, onInstallStarted, onClose,
}: {
  start?: WizardStep;
  /** undefined — оболочка ещё отвечает; null — оболочки нет. */
  engine: EngineStatus | null | undefined;
  endpoint: Endpoint | null;
  /** Идёт запись — движок не переустанавливаем. */
  recording: boolean;
  /** Перечитать состояние движка у оболочки (место, «установлен»). */
  onRefreshEngine: () => Promise<void>;
  /** Установка началась: мастер уже не «ничей», убирать его нельзя. */
  onInstallStarted?: () => void;
  /** «Пропустить мастер» или «Готово». */
  onClose: () => void;
}) {
  const [step, setStep] = useState<WizardStep>(start);
  const headingId = useId();
  const index = STEPS.findIndex((s) => s.id === step);
  const go = (id: WizardStep) => setStep(id);
  const next = () => go(STEPS[Math.min(index + 1, STEPS.length - 1)]!.id);
  // macOS — всегда «Apple Silicon»: видеокарты NVIDIA там не бывает.
  const profile: Profile = IS_MAC ? "mac" : engine?.gpu ? "cuda" : "cpu";
  const service = useService(endpoint, NEEDS_RESIDENT.includes(step));
  const [installing, setInstalling] = useState(false);
  const installPhase = (phase: InstallPhase) => {
    setInstalling(phase === "running");
    if (phase === "running") onInstallStarted?.();
  };

  let body;
  if (NEEDS_RESIDENT.includes(step) && !service.endpoint) {
    body = service.failed === "crashed" ? (
      <>
        <p className="error">Служба записи не запустилась</p>
        <p className="muted">
          Причина указана в журнале. Перезапустить службу можно из меню значка в области уведомлений.
        </p>
        <div className="wizard__bar">
          <Button variant="primary" onClick={() => void openLogs().catch((cause) => console.warn("open_logs:", cause))}>
            Открыть журнал
          </Button>
          <Button onClick={service.retry}>Подождать ещё</Button>
        </div>
      </>
    ) : service.failed ? (
      <>
        <p className="error">Служба записи не запустилась за 90 секунд.</p>
        <div className="wizard__bar">
          <Button variant="primary" onClick={service.retry}>Подождать ещё</Button>
        </div>
      </>
    ) : <p className="muted wizard__wait">Запуск службы записи…</p>;
  } else {
    const ep = service.endpoint!;
    switch (step) {
      case "hardware":
        body = <StepHardware engine={engine} profile={profile} onNext={next} />;
        break;
      case "engine":
        body = <StepEngine engine={engine} profile={profile} recording={recording}
          onRefresh={onRefreshEngine} onPhase={installPhase} onNext={next} />;
        break;
      case "hf":
        body = <StepHf endpoint={ep} onNext={next} onSkip={next} />;
        break;
      case "models":
        body = <StepModels endpoint={ep} onNext={next} />;
        break;
      case "devices":
        body = <StepDevices endpoint={ep} onNext={next} />;
        break;
      case "done":
        body = <StepDone onFinish={onClose} />;
        break;
    }
  }

  return (
    <div className="wizard">
      <div className="wizard__frame">
        <header className="wizard__head">
          <span className="eyebrow">Первый запуск</span>
          {step !== "done" && (
            <Button aria-label="Пропустить мастер" onClick={onClose} disabled={installing}
              title={installing ? "Дождитесь окончания установки"
                : "Мастер можно запустить снова: Настройки → Движок и модели"}>
              Пропустить
            </Button>
          )}
        </header>
        <ol className="wizard__steps" aria-label="Шаги мастера">
          {STEPS.map((s, i) => (
            <li key={s.id} aria-current={i === index ? "step" : undefined}
              className={i < index ? "is-done" : i === index ? "is-current" : undefined}>
              <span className="wizard__num">{i + 1}</span>
              <span className="wizard__name">{s.title}</span>
            </li>
          ))}
        </ol>
        <section className="wizard__step" aria-labelledby={headingId}>
          <h1 id={headingId}>{STEPS[index]!.title}</h1>
          {body}
        </section>
      </div>
    </div>
  );
}
