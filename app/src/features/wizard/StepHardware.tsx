import { estimateText, type Profile } from "../../lib/estimate";
import type { EngineStatus } from "../../lib/shell";
import { Button } from "../../ui/Button";

const PROFILE_TEXT: Record<Profile, string> = {
  cuda: "для видеокарты (CUDA)",
  cpu: "для процессора",
  mac: "для Apple Silicon (экспериментально)",
};

/** Шаг 1 «Ваш компьютер»: видеокарта, профиль движка, сколько ждать расшифровку. */
export function StepHardware({ engine, profile, onNext }: {
  engine: EngineStatus | null | undefined;
  profile: Profile;
  onNext: () => void;
}) {
  if (engine === undefined) return <p className="muted">Определяю видеокарту…</p>;
  return (
    <>
      <dl className="wizard__facts">
        <dt>{profile === "mac" ? "Компьютер" : "Видеокарта"}</dt>
        <dd>
          {profile === "mac" ? <span>Apple Silicon — распознавание на процессоре, разделение на спикеров — на графике (MPS)</span>
            : engine === null ? <span className="muted">сведения доступны только в приложении</span>
            : engine.gpu ? <span>{engine.gpu}</span>
            : <span className="muted">Видеокарта NVIDIA не найдена — расшифровка пойдёт на процессоре</span>}
        </dd>
        <dt>Движок</dt>
        <dd>{PROFILE_TEXT[profile]}</dd>
        <dt>Расшифровка</dt>
        <dd>
          <span>{estimateText(3600, profile)}</span>
          <span className="wizard__hint">оценка: на длинных записях обычно быстрее</span>
        </dd>
      </dl>
      <div className="wizard__bar">
        <Button variant="primary" onClick={onNext}>Далее</Button>
      </div>
    </>
  );
}
