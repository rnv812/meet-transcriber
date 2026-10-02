/**
 * «Запускать вместе с Windows» в настройках: то же, что на шаге «Готово»
 * мастера, но меняется сразу (это не настройка резидента — «Сохранить» её
 * не касается). Только в приложении, умеющем автозапуск.
 */

import { useEffect, useState } from "react";
import { errorText } from "../../lib/format";
import { OS_TEXT } from "../../lib/platform";
import { autostartAvailable, getAutostart, setAutostart } from "../../lib/shell";
import { Switch } from "./Section";

export function AutostartRow() {
  const [value, setValue] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    autostartAvailable()
      .then(async (ok) => {
        if (!ok) return;
        const saved = await getAutostart();
        if (live) setValue(saved ?? false);
      })
      .catch(() => {});
    return () => { live = false; };
  }, []);

  if (value === null) return null;

  const change = async (next: boolean) => {
    setError(null);
    setValue(next);
    try {
      await setAutostart(next);
    } catch (cause) {
      setValue(!next);
      setError(errorText(cause));
    }
  };

  return (
    <>
      <Switch label={OS_TEXT.autostart} hint={`Приложение запускается в ${OS_TEXT.trayArea} и отслеживает звонки`}
        value={value} onChange={(next) => void change(next)} />
      {error && <p className="error">{error}</p>}
    </>
  );
}
