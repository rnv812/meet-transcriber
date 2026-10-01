/**
 * Шаг 3 «Hugging Face»: модель спикеров закрыта принятием условий, поэтому
 * нужны три действия на сайте и токен. Без токена всё работает, только
 * расшифровка идёт без разделения на спикеров.
 */

import { useEffect, useState } from "react";
import { type Endpoint, type HfStatus, getHfStatus } from "../../lib/api";
import { openUrl } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { CheckFailure, HfTokenForm } from "../hf/HfTokenForm";
import { HF_MODEL_URL, HF_TOKENS_URL } from "../hf/links";

export function StepHf({ endpoint, onNext, onSkip }: {
  endpoint: Endpoint;
  onNext: () => void;
  onSkip: () => void;
}) {
  const [status, setStatus] = useState<HfStatus | null>(null);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    let live = true;
    getHfStatus(endpoint).then((s) => { if (live) setStatus(s); }).catch(() => {});
    return () => { live = false; };
  }, [endpoint]);

  const stored = status?.configured ? status.check : null;
  const ready = saved || stored?.ok === true;

  return (
    <>
      <p className="muted">
        Разделение на спикеров делает модель pyannote. Она бесплатная, но скачать её можно, только
        приняв условия на huggingface.co, — для этого нужен аккаунт и токен.
      </p>
      {status?.configured && !saved && (
        stored?.ok ? <p className="notice">Токен уже сохранён — доступ есть</p>
          : stored ? <CheckFailure check={stored} />
          : <p className="muted">Токен уже сохранён, но ещё не проверен</p>
      )}
      <ol className="wizard__todo">
        <li>
          <span>Войдите на huggingface.co, откройте страницу модели и нажмите «Agree and access repository».</span>
          <Button onClick={() => void openUrl(HF_MODEL_URL)}>Открыть страницу модели</Button>
        </li>
        <li>
          <span>
            Создайте токен. Подойдёт тип Read; для Fine-grained отметьте галочку
            «Read access to contents of all public gated repos you can access».
          </span>
          <Button onClick={() => void openUrl(HF_TOKENS_URL)}>Открыть настройки токенов</Button>
        </li>
        <li>
          <span>Вставьте токен и проверьте доступ. Токен хранится в диспетчере учётных данных Windows.</span>
          <HfTokenForm endpoint={endpoint} label="Токен" submitLabel="Проверить" onSaved={() => setSaved(true)} />
        </li>
      </ol>
      <div className="wizard__skip">
        <span className="muted">Без токена расшифровка будет без разделения на спикеров</span>
        <Button onClick={onSkip}>Пропустить</Button>
      </div>
      <div className="wizard__bar">
        <Button variant="primary" onClick={onNext} disabled={!ready}>Далее</Button>
      </div>
    </>
  );
}
