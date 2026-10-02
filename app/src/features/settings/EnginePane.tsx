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
import { PathText, Row } from "./Section";

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
      <Row label="Состояние" hint="Без движка встречи записываются, но не расшифровываются">
        <span className={engine.installed ? "tag tag--live" : "tag"}>{engine.installed ? "установлен" : "не установлен"}</span>
        {onReinstall && (
          <Button onClick={onReinstall}>{engine.installed ? "Переустановить движок" : "Установить движок…"}</Button>
        )}
      </Row>
      <Row label="Видеокарта" hint="Без видеокарты расшифровка идёт на процессоре и занимает больше времени">
        <span className="tag">{engine.gpu.available ? (engine.gpu.name ?? "есть") : "не найдена"}</span>
      </Row>
      <Row label="ffmpeg" hint="Нужен для записи и преобразования аудио">
        <span className={engine.ffmpeg ? "tag tag--live" : "tag"}>{engine.ffmpeg ? "есть" : "нет"}</span>
      </Row>
      <Row label="Компоненты" hint={engine.flavor === "cuda" ? "Сборка для видеокарты (CUDA)" : "Сборка для процессора (CPU)"}>
        <span className="tags">
          {engine.components.map((c) => (
            <span key={c.module} className={c.installed ? "tag tag--live" : "tag"}>
              {c.title}{!c.installed && c.note ? ` — ${c.note}` : ""}
            </span>
          ))}
        </span>
      </Row>
      <Row label="Расположение" hint="Окружение, в котором служба записи выполняет расшифровку">
        <span className="folder"><PathText path={engine.target} /></span>
      </Row>
    </>
  );
}
