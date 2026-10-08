/**
 * «Звонки в браузере» для автозаписи: какие браузеры отслеживать, строгий режим
 * и сайты звонков.
 *
 * Хранится отдельно от программ звонков (`auto_record.browsers`): у браузера
 * звонок — только занятый микрофон, а звук без микрофона (видео, музыка)
 * запись не запускает. Сайты звонков (`call_sites`) — подстроки заголовка
 * окна: по ним берётся название записи, а в строгом режиме
 * (`browser_require_site`) решается, звонок ли это вообще.
 */

import { X } from "lucide-react";
import { useState, type KeyboardEvent } from "react";
import { OS, type Os } from "../../lib/platform";
import { Button } from "../../ui/Button";
import { HelpTip, TipLine } from "../../ui/HelpTip";
import { Icon } from "../../ui/Icon";

export type Browser = { exe: string; title: string };

/** Распространённые браузеры Windows; имя exe — как в диспетчере задач. */
export const BROWSERS: Browser[] = [
  { exe: "chrome.exe", title: "Google Chrome" },
  { exe: "msedge.exe", title: "Microsoft Edge" },
  { exe: "firefox.exe", title: "Firefox" },
  { exe: "browser.exe", title: "Яндекс Браузер" },
  { exe: "opera.exe", title: "Opera" },
  { exe: "brave.exe", title: "Brave" },
  { exe: "vivaldi.exe", title: "Vivaldi" },
];

/** Браузеры macOS: имена процессов как в мониторинге системы (Яндекс Браузер — «Yandex»). */
const MAC_BROWSERS: Browser[] = [
  { exe: "Google Chrome", title: "Google Chrome" },
  { exe: "Microsoft Edge", title: "Microsoft Edge" },
  { exe: "firefox", title: "Firefox" },
  { exe: "Yandex", title: "Яндекс Браузер" },
  { exe: "Opera", title: "Opera" },
  { exe: "Brave Browser", title: "Brave" },
  { exe: "Vivaldi", title: "Vivaldi" },
];

export const browsersFor = (os: Os): Browser[] => (os === "macos" ? MAC_BROWSERS : BROWSERS);

const lower = (s: string) => s.toLowerCase();

/** Браузеры, в которых вы созваниваетесь (`auto_record.browsers`). */
export function BrowserCalls({ browsers, onBrowsers, os = OS }: {
  os?: Os;
  browsers: string[];
  onBrowsers: (v: string[]) => void;
}) {
  const selected = new Set(browsers.map(lower));
  const toggle = (exe: string) => onBrowsers(selected.has(lower(exe))
    ? browsers.filter((b) => lower(b) !== lower(exe))
    : [...browsers, exe]);

  return (
    <div className="callapps" role="group" aria-label="Звонки в браузере">
      <div className="callapps__head">
        <span className="srow__label">Звонки в браузере</span>
        <HelpTip label="Как распознаются звонки в браузере" title="Звонки в браузере">
          <TipLine>Запись начнётся, когда браузер использует микрофон. Музыка и видео без микрофона запись не запускают.</TipLine>
          <TipLine>
            Микрофон должен быть занят дольше 15 секунд: голосовой поиск и короткие голосовые сообщения запись не
            запускают.
          </TipLine>
          <TipLine>
            Начавшийся звонок продолжается, пока браузер использует микрофон или воспроизводит звук: веб-клиент с
            выключенным микрофоном может его освободить. Поэтому видео или музыка в том же браузере сразу после
            звонка тоже продлевают запись — её можно остановить вручную.
          </TipLine>
          <TipLine>
            Если в заголовке окна браузера есть сайт звонка, запись получит название по нему,
            например «Google Meet — Планёрка». Название можно изменить. Сайты звонков — в «Тонкой настройке».
          </TipLine>
        </HelpTip>
      </div>
      <span className="srow__hint">Отметьте браузеры, в которых вы созваниваетесь</span>
      <div className="callapps__grid">
        {browsersFor(os).map((b) => (
          <label key={b.exe} className="callapps__item">
            <input type="checkbox" aria-label={b.title} checked={selected.has(lower(b.exe))} onChange={() => toggle(b.exe)} />
            <span className="callapps__name">
              <span>{b.title}</span>
              <span className="callapps__exes">{b.exe}</span>
            </span>
          </label>
        ))}
      </div>
    </div>
  );
}

/**
 * Строгий режим (`browser_require_site`) и сайты звонков (`call_sites`) —
 * «Тонкая настройка» раздела «Автозапись».
 */
export function CallSites({ requireSite, sites, onRequireSite, onSites }: {
  requireSite: boolean; sites: string[];
  onRequireSite: (v: boolean) => void; onSites: (v: string[]) => void;
}) {
  const [draft, setDraft] = useState("");
  const typed = draft.trim();
  const duplicate = typed !== "" && sites.some((s) => lower(s) === lower(typed));
  const add = () => {
    if (!typed || duplicate) return;
    onSites([...sites, typed]);
    setDraft("");
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") { e.preventDefault(); add(); }
  };

  return (
    <div className="callapps" role="group" aria-label="Сайты звонков">
      <label className="callapps__item">
        <input type="checkbox" checked={requireSite} onChange={(e) => onRequireSite(e.target.checked)} />
        <span className="callapps__name">
          <span className="srow__label">Только если в заголовке окна сайт звонка</span>
          <span className="callapps__exes">
            Микрофон в браузере нужен не только для звонков: диктовка, голосовой поиск. В этом режиме они запись не запускают
          </span>
        </span>
      </label>
      <div className="callapps__head callapps__head--sub">
        <span className="srow__label">Сайты звонков</span>
        <HelpTip label="Что такое сайт звонка" title="Сайты звонков">
          <TipLine>
            Часть заголовка окна браузера, по которой узнаётся звонок, например «Google Meet» или «Телемост».
            Регистр букв не важен.
          </TipLine>
        </HelpTip>
      </div>
      <ul className="callapps__chips" aria-label="Сайты звонков">
        {sites.map((site) => (
          <li key={site} className="callchip">
            <span>{site}</span>
            <button type="button" className="callchip__remove" aria-label={`Убрать ${site}`}
              onClick={() => onSites(sites.filter((s) => s !== site))}><Icon as={X} size="sm" /></button>
          </li>
        ))}
      </ul>
      <div className="callapps__add">
        <span className="with-unit">
          <input type="text" aria-label="Новый сайт звонка" placeholder="Например, Моя платформа"
            value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={onKey} />
          <Button onClick={add} disabled={!typed || duplicate}>Добавить</Button>
        </span>
        {duplicate && <span className="muted callapps__note">Такой сайт уже есть в списке</span>}
      </div>
      {sites.length === 0 && (
        <p className="muted callapps__note">Не задано ни одного сайта — будет использован список по умолчанию.</p>
      )}
    </div>
  );
}
