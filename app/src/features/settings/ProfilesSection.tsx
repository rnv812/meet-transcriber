/**
 * Настройки «Профили людей» (`profiles`): по умолчанию выключены. Включение —
 * через подтверждение с пояснением, где хранятся профили и что это такое;
 * «Удалить все профили» действует сразу (с подтверждением), а не через
 * «Сохранить».
 */

import { useCallback, useEffect, useState } from "react";
import { deleteAllProfiles, getProfilesInfo, type Endpoint } from "../../lib/api";
import { errorText, plural } from "../../lib/format";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Row, Switch, type Raw, type SetFn } from "./Section";
import "./profiles.css";

/** Пояснение о приватности — дословно из спецификации. */
/** Честно о защите от лишнего: гарантий нет, поэтому есть «Скрыть». */
export const HONEST = "ИИ получает инструкцию не делать таких выводов, а результат дополнительно фильтруется; если что-то лишнее всё же появилось — скройте это утверждение";

export const PRIVACY_NOTE =
  "Профили хранятся только на этом компьютере. Это описание стиля общения по репликам, а не оценка личности.";

export function ProfilesTip() {
  return (
    <HelpTip label="Что такое профили людей" title="Профили людей">
      <TipLine>
        Для человека из базы голосов агент описывает, как он общается во встречах: стиль, что для него важно, как
        лучше строить разговор и чего избегать. Каждое наблюдение — со ссылками на реплики.
      </TipLine>
      <TipLine>
        Профиль составляется по кнопке в разделе «Голоса» → человек → «Профиль». Уже составленные профили обновляются
        сами после анализа встреч — не чаще раза в сутки и только если у человека появились новые реплики.
      </TipLine>
      <TipLine>
        Диагнозы, оценки человека и сведения о его личных обстоятельствах в профиле не нужны: {HONEST}.
      </TipLine>
      <TipLine>В базу знаний профили не выгружаются. Удалить их можно здесь же.</TipLine>
    </HelpTip>
  );
}

export function PcmTip() {
  return (
    <HelpTip label="Что такое раздел «Модель PCM»" title="Модель PCM">
      <TipLine>
        Process Communication Model (модель Тайби Кейлера) описывает шесть стилей общения: Логик, Упорный, Гармонизатор,
        Мечтатель, Бунтарь и Деятель. В профиле — гипотеза по репликам во встречах: какой стиль ведущий («база»),
        какой сейчас («фаза»), каким каналом с человеком лучше говорить и как давать признание.
      </TipLine>
      <TipLine>
        Это не сертифицированная оценка и не тест: раздел появляется, когда у человека не меньше 15 реплик в 3
        встречах. PCM — товарный знак Kahler Communications; модель упоминается только для описания.
      </TipLine>
    </HelpTip>
  );
}

export function ProfilesSection({ draft, set, endpoint }: { draft: Raw; set: SetFn; endpoint: Endpoint }) {
  const on = draft.profiles?.enabled === true;
  const [asking, setAsking] = useState(false);
  const [count, setCount] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const loadCount = useCallback(async () => {
    try { setCount((await getProfilesInfo(endpoint)).count); } catch { setCount(null); }
  }, [endpoint]);
  useEffect(() => { void loadCount(); }, [loadCount]);

  const toggle = (value: boolean) => {
    if (value) setAsking(true);
    else { setAsking(false); set("profiles", "enabled", false); }
  };
  const removeAll = async () => {
    setConfirmDelete(false);
    setError(null);
    try {
      const { deleted } = await deleteAllProfiles(endpoint);
      setNotice(deleted ? `Удалено профилей: ${deleted}` : "Профилей не было");
      await loadCount();
    } catch (e) {
      setError(errorText(e));
    }
  };

  return (
    <>
      <p className="muted sdesc">
        Описание того, как человек из базы голосов общается во встречах, — по его репликам, тем же агентом, что
        составляет итоги. По умолчанию выключено.
      </p>
      <Switch label="Составлять профили людей" help={<ProfilesTip />} hint={PRIVACY_NOTE}
        value={on || asking} onChange={toggle} />
      {asking && !on && (
        <div className="profiles-confirm" role="alertdialog" aria-label="Включить профили людей">
          <p>{PRIVACY_NOTE}</p>
          <p className="muted">
            Профиль — гипотеза по репликам во встречах. Диагнозы, оценки человека и сведения о возрасте, здоровье,
            религии и других личных обстоятельствах в нём не нужны: {HONEST}. Вкладка «Профиль» появится в
            разделе «Голоса».
          </p>
          <div className="profiles-confirm__row">
            <Button variant="primary" onClick={() => { setAsking(false); set("profiles", "enabled", true); }} autoFocus>
              Включить
            </Button>
            <Button onClick={() => setAsking(false)}>Отмена</Button>
          </div>
        </div>
      )}
      {on && (
        <Switch label="Раздел «Модель PCM»" help={<PcmTip />}
          hint="Гипотеза по Process Communication Model: «этажи», канал общения, как давать признание. Не сертифицированная оценка; нужна от 15 реплик в 3 встречах"
          value={draft.profiles?.pcm !== false} onChange={(x) => set("profiles", "pcm", x)} />
      )}
      <Row label="Удалить все профили"
        hint={count === null ? "Профили, ваши заметки о людях и индекс реплик на этом компьютере"
          : count === 0 ? "Сохранённых профилей нет"
            : `Сохранено: ${count} ${plural(count, "профиль", "профиля", "профилей")} — удалятся вместе с вашими `
              + "заметками и индексом реплик"}>
        {confirmDelete ? (
          <span className="confirm" role="alertdialog" aria-label="Удалить все профили">
            <span>Удалить без возврата?</span>
            <Button variant="danger" onClick={() => void removeAll()}>Удалить</Button>
            <Button onClick={() => setConfirmDelete(false)} autoFocus>Отмена</Button>
          </span>
        ) : (
          <Button variant="danger" onClick={() => { setNotice(null); setConfirmDelete(true); }} disabled={count === 0}>
            Удалить все профили
          </Button>
        )}
      </Row>
      {notice && <p className="notice" role="status">{notice}</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </>
  );
}
