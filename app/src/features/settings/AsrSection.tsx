/**
 * «Распознавание» по группам: «Когда расшифровывать»; «Устройство и движок» —
 * подбор под компьютер и на Windows устройство с движком и моделью для
 * видеокарты и процессора (AsrChoice), на Mac — что умеет этот Mac (видеоядра,
 * MLX Whisper; MacAccel); «Модель и язык» — на Mac одна модель на все встречи
 * (MacModel), на обеих ОС — язык речи; ниже — блок «Движок и модели»
 * (`children`, 0.6.2: прежде свой раздел); в «Тонкой настройке» внизу —
 * уточнение времени слов. Порог узнавания голоса и одновременная речь — в
 * «Спикерах», термины и исправления — в «Словаре» (0.4).
 *
 * Сверху (0.6.2) — «Как сейчас настроено» по сохранённым настройкам и
 * сведениям резидента, «Подобрать под этот компьютер» (asrFit): значения — в
 * черновик, пишет «Сохранить». Состояние движка и каталог моделей загружает
 * раздел (и перечитывает после загрузки модели и установки MLX).
 */

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { type Endpoint, type EngineState, type ModelsState, GIGAAM_PREFIX, getEngine, getModels } from "../../lib/api";
import { Button } from "../../ui/Button";
import { AsrChoice, backendOf } from "./AsrChoice";
import { asrFit, asrNow } from "./asrFit";
import { TextRow } from "./fields";
import { MacAccel, MacModel } from "./MacAsr";
import { macModelOf, macView } from "./macRoute";
import { FineTuning, FitPanel, NowLine, SettingsCard, Switch, type Raw, type SetFn } from "./Section";
import { AsrModelTip } from "./tips";

/**
 * Где используется модель по черновику «Распознавания» — для строки модели в
 * «Движке и моделях»: id → «видеокарта», «процессор — записи не на русском», …
 * На Mac (`mac`) — одна модель: «распознавание на этом Mac».
 */
export function modelUsage(draft: Raw, mac = false): Record<string, string[]> {
  const asr = draft.asr ?? {};
  const out: Record<string, string[]> = {};
  const add = (id: unknown, text: string) => {
    if (typeof id === "string" && id) (out[id] ??= []).push(text);
  };
  if (mac) {
    add(macModelOf(asr), "распознавание на этом Mac");
    return out;
  }
  for (const [device, title, key] of [["cuda", "видеокарта", "model"], ["cpu", "процессор", "cpu_model"]] as const) {
    if (backendOf(asr, device) === "gigaam") {
      add(GIGAAM_PREFIX + String(asr.gigaam_model ?? "v3_e2e_rnnt"), title);
      add(asr[key], `${title} — записи не на русском`);
    } else {
      add(asr[key], title);
    }
  }
  return out;
}

export function AsrSection({ draft, saved, set, endpoint, onOpenEngine, children }: {
  draft: Raw; saved: Raw; set: SetFn; endpoint: Endpoint;
  /** К блоку «Движок и модели» (скачать модели). */
  onOpenEngine: () => void;
  /** Блоки раздела перед «Тонкой настройкой» (движок и модели). */
  children?: ReactNode;
}) {
  const v = (k: string) => draft.asr?.[k];
  const [engine, setEngine] = useState<EngineState | null>(null);
  const [engineTried, setEngineTried] = useState(false);
  const [models, setModels] = useState<ModelsState | null>(null);
  const [modelsTried, setModelsTried] = useState(false);
  const loadEngine = useCallback(() => getEngine(endpoint).then(setEngine, () => {}), [endpoint]);
  const loadModels = useCallback(() => getModels(endpoint).then(setModels, () => {}), [endpoint]);
  useEffect(() => {
    let alive = true;
    void getEngine(endpoint).then((e) => { if (alive) setEngine(e); }, () => {})
      .finally(() => { if (alive) setEngineTried(true); });
    void getModels(endpoint).then((m) => { if (alive) setModels(m); }, () => {})
      .finally(() => { if (alive) setModelsTried(true); });
    return () => { alive = false; };
  }, [endpoint]);

  const mac = macView(engine);
  // Подбор ждёт и каталог: в нём размеры моделей и какие из них только русские.
  const fitState = !engineTried || !modelsTried ? "loading" : engine ? "ready" : "unavailable";
  const fit = fitState === "ready" && engine ? asrFit(engine, draft.asr, models) : null;
  const gigaamOnMac = mac && macModelOf(draft.asr).startsWith(GIGAAM_PREFIX);
  return (
    <>
      {engineTried && <NowLine parts={asrNow(saved, engine, models, mac)} />}
      <SettingsCard title="Когда расшифровывать">
        <Switch label="Расшифровывать сразу после записи" value={Boolean(draft.recording?.auto_transcribe)}
          hint="Включено — расшифровка встаёт в очередь, как только запись остановлена; выключено — по кнопке «Расшифровать» в карточке"
          onChange={(x) => set("recording", "auto_transcribe", x)} />
      </SettingsCard>
      <SettingsCard title="Устройство и движок">
        <FitPanel state={fitState} plan={fit?.plan ?? null}
          downloadNote={<>Скачать модели — в блоке{" "}
            <Button variant="link" onClick={onOpenEngine}>«Модели распознавания»</Button>.</>}
          onApply={() => { for (const [k, x] of Object.entries(fit?.values ?? {})) set("asr", k, x); }} />
        {mac ? <MacAccel engine={engine} onEngineChanged={() => void loadEngine()} /> : (
          <AsrChoice draft={draft} saved={saved} set={set} engine={engine} engineTried={engineTried} models={models}
            help={<AsrModelTip />} onOpenEngine={onOpenEngine} />
        )}
      </SettingsCard>
      <SettingsCard title={mac ? "Модель и язык" : "Язык"}>
        {mac && (
          <MacModel draft={draft} set={set} engine={engine} models={models} endpoint={endpoint}
            onModelsChanged={() => void loadModels()} onEngineChanged={() => void loadEngine()} />
        )}
        <TextRow id="asr-language" label="Язык речи" short
          hint={gigaamOnMac
            ? "Для моделей Whisper: ru, en…; auto — определить по записи. GigaAM распознаёт только русский"
            : mac ? "Код языка, например ru или en; auto — Whisper определит сам"
              : "Код языка, например ru или en; auto — определить по записи. GigaAM понимает только русский: другие языки распознаёт Whisper"}
          value={String(v("language") ?? "ru")} onChange={(x) => set("asr", "language", x)} />
      </SettingsCard>
      {children}
      <FineTuning>
        <Switch label="Уточнять время каждого слова" hint="Точнее границы реплик; расшифровка занимает немного больше времени. После GigaAM не нужно: время слов у него своё"
          value={Boolean(v("align"))} onChange={(x) => set("asr", "align", x)} />
      </FineTuning>
    </>
  );
}
