/**
 * Мастер первого запуска: железо → движок → Hugging Face → модели → запись →
 * ваш голос (необязательно) → условия → готово. Шаг условий — только пока они
 * не приняты (уже приняли — его нет в списке). Шаги 1–2 работают без резидента (команды оболочки); шагам 3–5 он
 * нужен — после установки оболочка поднимает его сама, мастер ждёт до 90 с.
 *
 * «Пропустить мастер» есть на каждом шаге: сам он больше не откроется (флаг
 * пишет `onClose`), снова — из Настройки → Приложение → «Мастер первого запуска».
 */

import { useEffect, useId, useState } from "react";
import { type Endpoint, getSettings, getState, resolveEndpoint } from "../../lib/api";
import type { Profile } from "../../lib/estimate";
import { IS_MAC } from "../../lib/platform";
import { type EngineStatus, openLogs, residentStatus } from "../../lib/shell";
import { termsAccepted } from "../../lib/terms";
import { X, Check } from "lucide-react";
import { Button } from "../../ui/Button";
import { Icon } from "../../ui/Icon";
import { MeetMark } from "../../ui/MeetMark";
import { Tip } from "../../ui/Tip";
import { StepDevices } from "./StepDevices";
import { StepDone } from "./StepDone";
import { type InstallPhase, StepEngine } from "./StepEngine";
import { StepHardware } from "./StepHardware";
import { StepHf } from "./StepHf";
import { StepModels } from "./StepModels";
import { StepTerms } from "./StepTerms";
import { StepVoice } from "./StepVoice";
import type { WizardStep } from "./useWizardGate";
// Переключатели, запись голоса и «?» шагов живут в настройках и берут их стили.
import "../settings/settings.css";
import "./wizard.css";

const STEPS: { id: WizardStep; title: string }[] = [
  { id: "hardware", title: "Ваш компьютер" },
  { id: "engine", title: "Установка движка" },
  { id: "hf", title: "Hugging Face" },
  { id: "models", title: "Модели" },
  { id: "devices", title: "Запись" },
  { id: "voice", title: "Ваш голос" },
  { id: "terms", title: "Условия" },
  { id: "done", title: "Готово" },
];
const NEEDS_RESIDENT: WizardStep[] = ["hf", "models", "devices", "voice", "terms"];

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

/**
 * Приняты ли условия текущей версии: undefined — ещё не знаем (резидента нет
 * или он не ответил; шаг тогда в списке). Читаем один раз, когда появится
 * резидент: согласие, данное в самом шаге, список шагов уже не меняет.
 */
function useTermsAccepted(endpoint: Endpoint | null): boolean | undefined {
  const [accepted, setAccepted] = useState<boolean | undefined>(undefined);
  const base = endpoint?.base ?? null;
  const token = endpoint?.token ?? null;
  const known = accepted !== undefined;
  useEffect(() => {
    if (base === null || known) return;
    let live = true;
    getSettings({ base, token })
      .then((s) => { if (live) setAccepted(termsAccepted(s)); })
      .catch((cause) => console.warn("ui.terms_accepted:", cause));
    return () => { live = false; };
  }, [base, token, known]);
  return accepted;
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
  // macOS — всегда «Apple Silicon»: видеокарты NVIDIA там не бывает.
  const profile: Profile = IS_MAC ? "mac" : engine?.gpu ? "cuda" : "cpu";
  const service = useService(endpoint, NEEDS_RESIDENT.includes(step));
  const accepted = useTermsAccepted(service.endpoint);
  const steps = accepted === true ? STEPS.filter((s) => s.id !== "terms") : STEPS;
  const shown: WizardStep = accepted === true && step === "terms" ? "done" : step;
  const index = steps.findIndex((s) => s.id === shown);
  const go = (id: WizardStep) => setStep(id);
  const next = () => go(steps[Math.min(index + 1, steps.length - 1)]!.id);
  const [installing, setInstalling] = useState(false);
  const installPhase = (phase: InstallPhase) => {
    setInstalling(phase === "running");
    if (phase === "running") onInstallStarted?.();
  };

  let body;
  if (NEEDS_RESIDENT.includes(shown) && !service.endpoint) {
    body = service.failed === "crashed" ? (
      <>
        <p className="wizard__error">Служба записи не запустилась</p>
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
        <p className="wizard__error">Служба записи не запустилась за 90 секунд.</p>
        <div className="wizard__bar">
          <Button variant="primary" onClick={service.retry}>Подождать ещё</Button>
        </div>
      </>
    ) : <p className="muted wizard__wait">Запуск службы записи…</p>;
  } else {
    const ep = service.endpoint!;
    switch (shown) {
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
      case "voice":
        body = <StepVoice endpoint={ep} onNext={next} />;
        break;
      case "terms":
        body = <StepTerms endpoint={ep} onNext={next} />;
        break;
      case "done":
        body = <StepDone onFinish={onClose} />;
        break;
    }
  }

  return (
    <div className="wizard aurora">
      <div className="wizard__frame">
        <aside className="wizard__side aurora-wash" aria-label="Первый запуск">
          <div className="wizard__brand">
            <MeetMark size={22} />
            <b>Первый запуск</b>
          </div>
          <ol className="wizard__steps" aria-label="Шаги мастера">
            {steps.map((s, i) => (
              <li key={s.id} aria-current={i === index ? "step" : undefined}
                className={i < index ? "is-done" : i === index ? "is-current" : undefined}>
                <span className="wizard__num">
                  {i < index ? <Icon as={Check} size="sm" /> : i + 1}
                </span>
                {s.title}
                {i < index && <span className="wizard__sr">пройден</span>}
              </li>
            ))}
          </ol>
          {shown !== "done" && (
            <Tip content={installing ? "Дождитесь окончания установки"
              : "Мастер можно запустить снова: Настройки → Приложение → «Мастер первого запуска»"}>
              <Button className="wizard__skip-all" variant="ghost" icon={X} aria-label="Пропустить мастер"
                onClick={onClose} disabled={installing}>
                Пропустить мастер
              </Button>
            </Tip>
          )}
        </aside>
        <section className="wizard__step" aria-labelledby={headingId}>
          <header className="wizard__title">
            <span className="wizard__count">Шаг {index + 1} из {steps.length}</span>
            <h1 id={headingId}>{steps[index]!.title}</h1>
          </header>
          {body}
        </section>
      </div>
    </div>
  );
}
