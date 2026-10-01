/**
 * Шаг 6 «Готово»: где живёт приложение и автозапуск с Windows. Переключатель
 * показывается, только если оболочка умеет автозапуск (`set_autostart`), и
 * начинает с сохранённого выбора (повторный проход мастера его не сбивает);
 * выбора не было — «вкл».
 */

import { useEffect, useState } from "react";
import { autostartAvailable, getAutostart, setAutostart } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { Switch } from "../settings/Section";

export function StepDone({ onFinish }: { onFinish: () => void }) {
  const [available, setAvailable] = useState(false);
  const [autostart, setAutostartValue] = useState(true);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    autostartAvailable()
      .then(async (ok) => {
        if (!live) return;
        setAvailable(ok);
        const saved = ok ? await getAutostart() : null;
        if (live && saved !== null) setAutostartValue(saved);
      })
      .catch(() => {});
    return () => { live = false; };
  }, []);

  const finish = async () => {
    setBusy(true);
    if (available) {
      // Автозапуск не критичен: не вышло — не держим человека в мастере.
      await setAutostart(autostart).catch((cause) => console.warn("set_autostart:", cause));
    }
    onFinish();
  };

  return (
    <>
      <p className="wizard__lead">Приложение живёт в трее. Клик по иконке — окно, правый клик — запись</p>
      <p className="muted">
        Мастер можно запустить снова: Настройки → Движок и модели → «Запустить мастер».
      </p>
      {available && (
        <Switch label="Запускать вместе с Windows" hint="приложение тихо стартует в трее и ловит звонки"
          value={autostart} onChange={setAutostartValue} />
      )}
      <div className="wizard__bar">
        <Button variant="primary" onClick={() => void finish()} disabled={busy}>Готово</Button>
      </div>
    </>
  );
}
