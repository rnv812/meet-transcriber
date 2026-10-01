/**
 * Движок в настройках — только сведения (что видит резидент). Ставит и
 * переставляет движок оболочка, через мастер: у неё своя проверка места и
 * защита идущей записи. `POST /engine/install` резидента остаётся для CLI,
 * в окне его нет.
 */

import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type EngineState, getEngine } from "../../lib/api";
import { errorText } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Row } from "./Section";

export function EnginePane({ endpoint, onReinstall }: {
  endpoint: Endpoint;
  /** Открыть мастер на шаге движка. */
  onReinstall?: () => void;
}) {
  const [engine, setEngine] = useState<EngineState | null>(null);
  const [tried, setTried] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try { setEngine(await getEngine(endpoint)); setError(null); }
    catch (e) { setEngine(null); setError(`Движок не загрузился: ${errorText(e)}`); }
    finally { setTried(true); }
  }, [endpoint]);

  useEffect(() => { void load(); }, [load]);

  if (!engine) {
    return <>{error && <p className="error">{error}</p>}<p className="muted">{tried ? "Нет данных." : "Загружаю…"}</p></>;
  }
  return (
    <>
      <Row label="Состояние" hint="без движка приложение пишет встречи, но не расшифровывает их">
        <span className={engine.installed ? "tag tag--live" : "tag"}>{engine.installed ? "установлен" : "не установлен"}</span>
        {onReinstall && (
          <Button onClick={onReinstall}>{engine.installed ? "Переустановить движок" : "Установить движок…"}</Button>
        )}
      </Row>
      <Row label="Видеокарта" hint="без неё расшифровка идёт на процессоре и заметно дольше">
        <span className="tag">{engine.gpu.available ? (engine.gpu.name ?? "есть") : "не найдена"}</span>
      </Row>
      <Row label="ffmpeg" hint="нужен и для записи, и для конвертации дорожек">
        <span className={engine.ffmpeg ? "tag tag--live" : "tag"}>{engine.ffmpeg ? "есть" : "нет"}</span>
      </Row>
      <Row label="Компоненты" hint={`вариант ${engine.flavor === "cuda" ? "для видеокарты" : "для процессора"}`}>
        <span className="tags">
          {engine.components.map((c) => (
            <span key={c.module} className={c.installed ? "tag tag--live" : "tag"}>{c.title}</span>
          ))}
        </span>
      </Row>
      <Row label="Где" hint="окружение, которым резидент запускает задачи"><code className="path">{engine.target}</code></Row>
    </>
  );
}
