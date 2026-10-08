/**
 * Движок в настройках — только сведения (что видит резидент). Ставит и
 * переставляет движок оболочка, через мастер: у неё своя проверка места и
 * защита идущей записи. `POST /engine/install` резидента остаётся для CLI,
 * в окне его нет.
 *
 * Раздел «Движок и модели» (EngineSection): состояние движка, где хранить,
 * загрузка моделей и их папки. Выбор движка и модели — в «Распознавании»,
 * токен Hugging Face — в «Спикерах», мастер — в «Приложении» (0.4).
 */

import { useCallback, useEffect, useState } from "react";
import { type Endpoint, type EngineState, getEngine } from "../../lib/api";
import { errorText } from "../../lib/format";
import { Button } from "../../ui/Button";
import { Loading } from "../../ui/Loading";
import { ModelsPane } from "./ModelsPane";
import { PathText, Row, SeeAlso, SettingsCard, type Raw } from "./Section";
import { StoragePane } from "./StoragePane";
import { modelUsage } from "./AsrSection";

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
    return <>{error && <p className="error">{error}</p>}
      {tried ? <p className="muted">Нет данных.</p> : <Loading label="Проверяю движок…" />}</>;
  }
  return (
    <>
      <Row label="Состояние" hint="Без движка встречи записываются, но не расшифровываются">
        <span className={`badge ${engine.installed ? "badge--fresh" : "badge--error"}`}>
          {engine.installed ? "установлен" : "не установлен"}
        </span>
        {onReinstall && (
          <Button onClick={onReinstall}>{engine.installed ? "Переустановить движок" : "Установить движок…"}</Button>
        )}
      </Row>
      <Row label="Видеокарта" hint="Без видеокарты расшифровка идёт на процессоре и занимает больше времени">
        <span className={`badge ${engine.gpu.available ? "badge--plain" : "badge--stale"}`}>
          {engine.gpu.available ? (engine.gpu.name ?? "есть") : "не найдена"}
        </span>
      </Row>
      <Row label="ffmpeg" hint="Нужен для записи и преобразования аудио">
        <span className={`badge ${engine.ffmpeg ? "badge--fresh" : "badge--error"}`}>{engine.ffmpeg ? "есть" : "нет"}</span>
      </Row>
      <Row label="Компоненты" stack hint={engine.flavor === "cuda" ? "Сборка для видеокарты (CUDA)"
        : engine.flavor === "mac" ? "Сборка для Apple Silicon (экспериментально)" : "Сборка для процессора (CPU)"}>
        <span className="tags tags--start">
          {engine.components.map((c) => (
            <span key={c.module} className={`badge ${c.installed ? "badge--plain" : "badge--error"}`}>{c.title}</span>
          ))}
        </span>
        {/* Почему компонента нет — строкой под бейджами, а не внутри бейджа. */}
        {engine.components.filter((c) => !c.installed && c.note).map((c) => (
          <span key={c.module} className="srow__hint engine__note">{c.title}: {c.note}</span>
        ))}
      </Row>
      <Row label="Расположение" hint="Окружение, в котором служба записи выполняет расшифровку">
        <span className="folder"><PathText path={engine.target} /></span>
      </Row>
    </>
  );
}

export function EngineSection({ endpoint, draft, onReinstall, onOpenAsr, onOpenSpeakers }: {
  endpoint: Endpoint;
  /** Черновик настроек: где выбрана модель (по «Распознаванию»). */
  draft: Raw;
  onReinstall?: () => void;
  onOpenAsr: () => void;
  onOpenSpeakers: () => void;
}) {
  return (
    <>
      <SeeAlso head>
        Выбор движка и модели — в разделе{" "}
        <Button variant="link" onClick={onOpenAsr}>«Распознавание»</Button>.
      </SeeAlso>
      <SettingsCard title="Движок">
        <EnginePane endpoint={endpoint} onReinstall={onReinstall} />
      </SettingsCard>
      <SettingsCard title="Хранение">
        <StoragePane endpoint={endpoint} />
      </SettingsCard>
      <SettingsCard title="Модели">
        <ModelsPane endpoint={endpoint} usage={modelUsage(draft)} />
        <SeeAlso>
          Токен Hugging Face для закрытых моделей — в разделе{" "}
          <Button variant="link" onClick={onOpenSpeakers}>«Спикеры»</Button>.
        </SeeAlso>
      </SettingsCard>
    </>
  );
}
