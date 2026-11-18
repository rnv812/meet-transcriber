/**
 * «Запускать вместе с Windows» в настройках: то же, что на шаге «Готово»
 * мастера, но меняется сразу (это не настройка резидента — «Сохранить» её
 * не касается). Только в приложении, умеющем автозапуск.
 *
 * macOS: если Meet выключен в «Системные настройки → Объекты входа», под
 * переключателем — строка с кнопкой в этот раздел (из Meet это не включить).
 */

import { useCallback, useEffect, useState } from "react";
import { errorText } from "../../lib/format";
import { IS_MAC, OS_TEXT } from "../../lib/platform";
import { autostartAvailable, autostartBlocked, getAutostart, openPrivacyPane, setAutostart } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { Row, Switch } from "./Section";

export function AutostartRow() {
  const [value, setValue] = useState<boolean | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [blocked, setBlocked] = useState(false);

  const checkBlocked = useCallback(async () => {
    if (!IS_MAC) return;
    setBlocked(await autostartBlocked());
  }, []);

  useEffect(() => {
    let live = true;
    autostartAvailable()
      .then(async (ok) => {
        if (!ok) return;
        const saved = await getAutostart();
        if (!live) return;
        setValue(saved ?? false);
        if (IS_MAC) {
          const off = await autostartBlocked();
          if (live) setBlocked(off);
        }
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
    await checkBlocked();
  };

  return (
    <>
      <Switch label={OS_TEXT.autostart} hint={`Приложение запускается в ${OS_TEXT.trayArea} и отслеживает звонки`}
        value={value} onChange={(next) => void change(next)} />
      {value && blocked && (
        <Row label="Выключено в системных настройках"
          hint="Meet снят в «Объектах входа» — macOS не откроет Meet при входе, пока его там не включат">
          <Button onClick={() => void openPrivacyPane("login_items")}>Открыть «Объекты входа»</Button>
        </Row>
      )}
      {error && <p className="error">{error}</p>}
    </>
  );
}
