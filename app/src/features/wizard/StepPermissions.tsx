/**
 * Шаг «Разрешения» (только macOS, после «Запись»): «Микрофон» и «Запись
 * экрана» выдаются здесь, до первой встречи, а не системными окнами посреди
 * звонка (часто — автозаписи без участия человека). Без разрешений шаг не
 * запирает: «Далее» доступна всегда.
 */

import type { Endpoint } from "../../lib/api";
import { Button } from "../../ui/Button";
import { PermissionRows } from "../settings/PermissionRows";

export function StepPermissions({ endpoint, onNext }: { endpoint: Endpoint; onNext: () => void }) {
  return (
    <>
      <p className="muted">
        Meet нужны два разрешения macOS: микрофон — для вашего голоса, запись экрана — для звука собеседников
        (видео Meet не записывает). Выдайте их сейчас, чтобы системные вопросы не появились посреди встречи.
      </p>
      <PermissionRows endpoint={endpoint} />
      <p className="wizard__hint">
        После включения записи экрана macOS предложит «Завершить и открыть снова» — здесь это безопасно: запись не
        идёт. Meet откроется снова, а мастер можно продолжить из Настройки → Приложение → «Мастер первого запуска».
      </p>
      <div className="wizard__bar">
        <Button variant="primary" onClick={onNext}>Далее</Button>
      </div>
    </>
  );
}
