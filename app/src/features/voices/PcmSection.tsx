/**
 * Раздел «Модель PCM» вкладки «Профиль»: гипотеза по Process Communication
 * Model по репликам во встречах — «этажи» (база внизу, как в модели), фаза,
 * восприятие и канал общения с готовыми фразами, «Как давать признание»,
 * признаки напряжения и «Как вернуть в конструктив», «Как строить разговор».
 * Подписан прямо: гипотеза, не сертифицированная оценка.
 */

import type { Pcm, Profile } from "../../lib/types";
import { floorsText, PCM_FLOOR_MAX, PCM_HYPOTHESIS, PCM_LABELS, pcmFloors, pcmLabel, percent } from "../../lib/pcm";
import { HideButton, RefChips, type OpenAt as Open } from "./RefChips";
import { Truncate } from "../../ui/Truncate";
import "./pcm.css";

/** «Этажи»: шесть строк, база — нижняя, полоса 0–5, отмечены база и фаза. */
export function PcmFloors({ pcm }: { pcm: Pcm }) {
  const floors = pcmFloors(pcm);
  return (
    <figure className="pcm-house" role="img" aria-label={floorsText(pcm)}>
      <div className="pcm-house__roof" aria-hidden="true" />
      {[...floors].reverse().map((f) => (
        <div key={f.type} aria-hidden="true" data-type={f.type}
          className={`pcm-floor${f.base ? " pcm-floor--base" : ""}${f.phase ? " pcm-floor--phase" : ""}`}>
          <Truncate className="pcm-floor__name" text={`${PCM_LABELS[f.type].ru} (${PCM_LABELS[f.type].en})`}>
            {PCM_LABELS[f.type].ru} <span className="pcm-floor__en">({PCM_LABELS[f.type].en})</span>
          </Truncate>
          <span className="pcm-floor__bar">
            {Array.from({ length: PCM_FLOOR_MAX }, (_, k) => (
              <span key={k} className={`pcm-floor__cell${k < f.value ? " pcm-floor__cell--on" : ""}`} />
            ))}
          </span>
          <span className="pcm-floor__marks">
            {f.base && <span className="pcm-mark">база</span>}
            {f.phase && <span className="pcm-mark pcm-mark--phase">фаза</span>}
          </span>
        </div>
      ))}
      <div className="pcm-house__ground" aria-hidden="true" />
    </figure>
  );
}

function Claim({ title, claim, profile, onOpenAt }: {
  title: string; claim: Pcm["base"]; profile: Profile; onOpenAt: Open;
}) {
  return (
    <div className="pcm-fact">
      <span className="pcm-fact__k">{title}</span>
      <span className="pcm-fact__v">{pcmLabel(claim.type)}</span>
      <span className="pcm-fact__c">уверенность {percent(claim.confidence)}</span>
      <RefChips profile={profile} refs={claim.refs} onOpenAt={onOpenAt} />
    </div>
  );
}

export function PcmSection({ profile, note, onOpenAt, onHide }: {
  profile: Profile; note?: string; onOpenAt: Open; onHide?: (text: string) => void;
}) {
  // У гипотезы не осталось живой опоры (встречи удалили, реплики изменились) — как будто её нет.
  const pcm = profile.pcm && profile.pcm.base.refs.some((r) => !r.stale) ? profile.pcm : undefined;
  const head = (
    <div className="pcm__head">
      <h4 id="pcm-h" className="profile__h pcm__title">Модель PCM</h4>
      <span className="pcm__badge">{PCM_HYPOTHESIS}</span>
    </div>
  );
  if (!pcm) {
    return (
      <section className="pcm pcm--empty" aria-labelledby="pcm-h">
        {head}
        <p className="pcm__empty">{note ?? "Гипотеза появится после обновления профиля."}</p>
      </section>
    );
  }
  return (
    <section className="pcm" aria-labelledby="pcm-h">
      {head}
      <div className="pcm__grid">
        <PcmFloors pcm={pcm} />
        <div className="pcm__facts">
          <Claim title="База" claim={pcm.base} profile={profile} onOpenAt={onOpenAt} />
          {pcm.phase && <Claim title="Фаза" claim={pcm.phase} profile={profile} onOpenAt={onOpenAt} />}
          {pcm.perception && (
            <div className="pcm-fact">
              <span className="pcm-fact__k">Восприятие</span>
              <span className="pcm-fact__v">{pcm.perception.value}</span>
              <RefChips profile={profile} refs={pcm.perception.refs} onOpenAt={onOpenAt} />
            </div>
          )}
          {pcm.channel && (
            <div className="pcm-fact">
              <span className="pcm-fact__k">Канал общения</span>
              <span className="pcm-fact__v">{pcm.channel.value}</span>
              {pcm.channel.examples.length > 0 && (
                <ul className="pcm__phrases" aria-label="Как можно сказать">
                  {pcm.channel.examples.map((e, n) => <li key={n}>«{e}»</li>)}
                </ul>
              )}
            </div>
          )}
        </div>
      </div>
      <div className="pcm__cards">
        {pcm.needs && (
          <section className="profile__card" aria-label="Как давать признание">
            <h4 className="profile__h">Как давать признание</h4>
            {pcm.needs.value && <p className="profile__text">{pcm.needs.value}</p>}
            {pcm.needs.how_to_recognize && <p className="profile__text">{pcm.needs.how_to_recognize}</p>}
          </section>
        )}
        {(pcm.stress_signs?.length || pcm.back_to_constructive?.length) ? (
          <section className="profile__card profile__card--avoid" aria-label="Признаки напряжения">
            <h4 className="profile__h">Признаки напряжения во встречах</h4>
            <ul className="profile__list">
              {(pcm.stress_signs ?? []).map((s, n) => (
                <li key={n} className="profile__item">
                  <div className="profile__line">
                    <p className="profile__text">{s.text}</p>
                    {onHide && <HideButton onHide={() => onHide(s.text)} />}
                  </div>
                  <RefChips profile={profile} refs={s.refs} onOpenAt={onOpenAt} />
                </li>
              ))}
            </ul>
            {(pcm.back_to_constructive ?? []).length > 0 && (
              <>
                <h4 className="profile__h pcm__sub">Как вернуть в конструктив</h4>
                <ul className="pcm__advice">
                  {pcm.back_to_constructive!.map((a, n) => <li key={n}>{a}</li>)}
                </ul>
              </>
            )}
          </section>
        ) : null}
        {(pcm.conversation ?? []).length > 0 && (
          <section className="profile__card profile__card--how_to_talk" aria-label="Как строить разговор">
            <h4 className="profile__h">Как строить разговор</h4>
            <ul className="pcm__advice">
              {pcm.conversation!.map((a, n) => <li key={n}>{a}</li>)}
            </ul>
          </section>
        )}
      </div>
      <p className="profile__foot">
        Process Communication Model — модель Тайби Кейлера; PCM — товарный знак Kahler Communications. Модель здесь
        упоминается только для описания; это не её официальная оценка.
      </p>
    </section>
  );
}
