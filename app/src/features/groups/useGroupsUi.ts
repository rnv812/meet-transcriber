/**
 * Группы встреч в окне — одно состояние на окно (его создаёт App): список групп
 * с резидента (useGroups), область списка (вся библиотека, группа, «Без
 * группы») с запоминанием, действия с группами и встречами, окно названия
 * группы, уведомление с «Отменить» и перетаскивание.
 *
 * Левая панель (GroupsNav), заголовок группы (GroupHeader), меню записи,
 * панель выбора и слой окна (GroupsLayer) получают это состояние целиком.
 *
 * Старый резидент без `/groups` — `shown: false`: интерфейс групп не виден,
 * область в фильтр не идёт. Файл групп от более новой версии Meet — `readOnly`:
 * группы не создать, не переименовать, не удалить и не переставить (перенос
 * встреч — правка их meta.json — остаётся). `/groups` не ответил (не 404) —
 * `unavailable`: список не сужается, в панели — «Группы недоступны» с повтором.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  createGroup, deleteGroup, type Endpoint, getAssistant, getGroups, getRecordings, libraryFilterKey, orderGroups,
  patchGroup, setGroupMembers,
} from "../../lib/api";
import { errorText } from "../../lib/format";
import { kbJoin, kbRelative } from "../../lib/kb";
import { pickFolder } from "../../lib/shell";
import {
  isScope, loadGroupScope, NO_GROUP, NO_GROUP_NAME, reorderIds, saveGroupScope, sentence, shiftId, UNKNOWN_NAME,
  type GroupScope,
} from "../../lib/groups";
import type { GroupInfo, GroupMembersResult, GroupWrite, LibraryFilter, UnknownGroup } from "../../lib/types";
import { useGroups } from "../../state/useGroups";
import { createGroupDrag, type DragPayload, type DropTarget, type GroupDrag } from "./drag";
import { allRow, focusInRow, focusWhenReady, groupRow, listSearch, meetingRow, rescueFocus } from "./focus";

/** Сколько живёт уведомление с «Отменить», мс (пока на нём указатель или фокус — не уходит). */
export const UNDO_MS = 8000;
/** После паузы (указатель или фокус ушли) уведомлению остаётся не меньше, мс. */
const RESUME_MIN_MS = 2000;

/** Окно названия группы: новая (потом, может быть, перенести в неё встречи), переименовать, назвать неизвестную. */
export type GroupDialogState =
  | { mode: "create"; then?: (id: string, name: string) => void }
  | { mode: "rename"; id: string; name: string; color: string }
  | { mode: "name"; id: string };

/** Подтверждение «Убрать из встреч» у неизвестной группы (отменить нельзя). */
export type ClearAsk = { id: string; count: number };

export type GroupToast = { n: number; text: string; error?: boolean; undo?: () => Promise<void> };

export type GroupsUi = {
  /** Интерфейс групп виден: резидент их умеет (ответ `/groups` пришёл). */
  shown: boolean;
  /** Умеет ли резидент группы: null — ещё не знаем, false — старый (404). */
  supported: boolean | null;
  /** `/groups` не ответил (не 404) и групп ещё не знаем: список не сужается, панель предлагает повторить. */
  unavailable: boolean;
  retry: () => void;
  groups: GroupInfo[];
  unknown: UnknownGroup[];
  /** Встреч без группы и всего (с поиском и фильтром — среди найденного). */
  none: number;
  total: number;
  /** Файл групп от более новой версии Meet: группы только смотреть. */
  readOnly: boolean;
  /** Файл групп повреждён (`copy` — куда отложен, если уже отложен). */
  broken: { copy: string | null } | null;
  dismissBroken: () => void;

  scope: GroupScope;
  setScope: (scope: GroupScope) => void;
  /** Имя области для заголовка и подсказки поиска; все записи — null. */
  scopeName: string | null;
  scopeCount: number | null;
  /**
   * Область для фильтра библиотеки (`groups=`): id группы или NO_GROUP; null — все записи. Только
   * когда `/groups` ответил и такая группа есть: иначе список не сужается молча (без заголовка и
   * панели, где область видно и можно снять). Одно значение — его и сужает `withGroupScope` поиска.
   */
  libraryScope: GroupScope;

  /** Имя группы по id: null и NO_GROUP — «Без группы», нет в списке — «Группа без названия». */
  nameOf: (id: string | null) => string;

  create: (then?: (id: string, name: string) => void) => void;
  rename: (id: string) => void;
  nameUnknown: (id: string) => void;
  setColor: (id: string, color: string) => Promise<void>;
  /**
   * «Папка базы знаний…»: выбрать папку внутри базы знаний (диалог оболочки) —
   * ассистент встреч группы видит её структуру целиком первой. Базы нет или
   * папка вне неё — уведомление.
   */
  pickKbFolder: (id: string) => Promise<void>;
  /** Убрать папку базы знаний группы. */
  clearKbFolder: (id: string) => Promise<void>;
  /** Группа на шаг выше/ниже; true — порядок поменялся. */
  shift: (id: string, delta: -1 | 1) => Promise<boolean>;
  /** Новый порядок; true — поменялся (тот же порядок или ошибка — false). */
  reorder: (ids: string[]) => Promise<boolean>;
  remove: (id: string) => Promise<void>;
  clearUnknown: (id: string) => void;
  /**
   * Перенести встречи в группу (null — убрать из группы); `prev` — их группы до переноса (для
   * «Отменить»); `name` — имя группы, если её только что создали и в списке её ещё нет.
   */
  moveMeetings: (ids: string[], prev: Record<string, string | null>, group: string | null, name?: string) => Promise<void>;

  drag: GroupDrag;

  dialog: GroupDialogState | null;
  closeDialog: () => void;
  /** Сохранить окно названия: null — получилось, иначе текст ошибки (окно остаётся). */
  submitDialog: (name: string, color: string) => Promise<string | null>;
  clearAsk: ClearAsk | null;
  answerClear: (ok: boolean) => void;
  toast: GroupToast | null;
  closeToast: () => void;
  /** Указатель или фокус на уведомлении — время не идёт (true), ушли — идёт дальше (false). */
  holdToast: (on: boolean) => void;
};

const NO_LIST: GroupInfo[] = [];
const NO_UNKNOWN: UnknownGroup[] = [];

/**
 * `tick` — когда перечитать группы (смена содержимого библиотеки и `groups.changed`);
 * `q` и `filter` — счётчики среди найденного; `onLibraryChanged` — встречи сменили
 * группу: список перечитывается сразу, не дожидаясь события.
 */
export function useGroupsUi(ep: Endpoint | null, tick: number, q: string, filter: LibraryFilter,
  onLibraryChanged?: () => void): GroupsUi {
  const info = useGroups(ep, tick, q, filter);
  const shown = info.supported === true;
  const groups = shown ? info.groups : NO_LIST;
  const unknown = shown ? info.unknown : NO_UNKNOWN;
  const none = shown ? info.none : 0;
  const total = groups.reduce((n, g) => n + g.count, 0) + unknown.reduce((n, g) => n + g.count, 0) + none;
  const unavailable = info.supported === null && info.failed;
  const retry = useCallback(() => void info.refresh(), [info.refresh]);

  const [scope, setScopeState] = useState<GroupScope>(loadGroupScope);
  const setScope = useCallback((next: GroupScope) => {
    const value = isScope(next) ? next : null;
    setScopeState(value);
    saveGroupScope(value);
  }, []);

  // Область — среди ответа `/groups`. С поиском или любым фильтром неизвестные id без найденных
  // встреч в ответ не попадают: тогда область верим, а есть ли группа — спрашиваем без фильтра.
  const filtered = Boolean(q.trim()) || Boolean(libraryFilterKey(filter));
  const known = !scope || scope === NO_GROUP || groups.some((g) => g.id === scope) || unknown.some((g) => g.id === scope);
  useEffect(() => {
    if (!shown || known || !scope) return;
    if (!filtered) { setScope(null); return; }
    let gone = false;
    getGroups(ep!).then((all) => {
      if (gone) return;
      if (!all.groups.some((g) => g.id === scope) && !all.unknown.some((g) => g.id === scope)) setScope(null);
    }).catch(() => { /* не узнали — область остаётся, проверим со следующим ответом */ });
    return () => { gone = true; };
  }, [ep, shown, known, scope, filtered, setScope]);

  const nameOf = useCallback((id: string | null) => {
    if (id === null || id === NO_GROUP) return NO_GROUP_NAME;
    return groups.find((g) => g.id === id)?.name ?? UNKNOWN_NAME;
  }, [groups]);

  const scopeName = shown && scope ? nameOf(scope) : null;
  const scopeCount = !shown || !scope ? null : scope === NO_GROUP ? none
    : groups.find((g) => g.id === scope)?.count ?? unknown.find((g) => g.id === scope)?.count ?? 0;
  const libraryScope = shown && scope && (known || filtered) ? scope : null;

  // --- уведомление ----------------------------------------------------------------

  const [toast, setToast] = useState<GroupToast | null>(null);
  const toastN = useRef(0);
  const say = useCallback((text: string, undo?: () => Promise<void>) => {
    setToast({ n: ++toastN.current, text, undo });
  }, []);
  const fail = useCallback((cause: unknown) => {
    setToast({ n: ++toastN.current, text: sentence(errorText(cause)), error: true });
  }, []);
  const closeToast = useCallback(() => setToast(null), []);
  // Таймер с паузой: пока указатель или фокус на уведомлении — время не идёт (WCAG 2.2.1).
  const timer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const left = useRef(UNDO_MS);
  const since = useRef(0);
  const held = useRef(false);
  const shownToast = useRef<GroupToast | null>(null);
  shownToast.current = toast;
  const arm = useCallback((n: number) => {
    clearTimeout(timer.current);
    since.current = Date.now();
    timer.current = setTimeout(() => setToast((cur) => (cur?.n === n ? null : cur)), left.current);
  }, []);
  useEffect(() => {
    if (!toast) return;
    left.current = UNDO_MS;
    held.current = false;
    arm(toast.n);
    return () => clearTimeout(timer.current);
  }, [toast, arm]);
  const holdToast = useCallback((on: boolean) => {
    const t = shownToast.current;
    if (!t || on === held.current) return;
    held.current = on;
    if (on) {
      clearTimeout(timer.current);
      left.current = Math.max(RESUME_MIN_MS, left.current - (Date.now() - since.current));
    } else arm(t.n);
  }, [arm]);

  // --- повреждённый файл ------------------------------------------------------------

  /** Запись отложила повреждённый файл: куда (показывается, пока не закроют). */
  const [moved, setMoved] = useState<string | null>(null);
  const [hideBroken, setHideBroken] = useState(false);
  // Файл снова повредился после того, как предупреждение закрыли, — показать снова.
  const wasBroken = useRef(info.broken);
  useEffect(() => {
    if (info.broken && !wasBroken.current) setHideBroken(false);
    wasBroken.current = info.broken;
  }, [info.broken]);
  const noteMoved = useCallback((got: GroupWrite | null | undefined) => {
    if (got?.moved_broken) { setMoved(got.moved_broken); setHideBroken(false); }
  }, []);
  const broken = !shown || hideBroken ? null
    : info.broken ? { copy: info.brokenCopy } : moved ? { copy: moved } : null;
  const dismissBroken = useCallback(() => { setHideBroken(true); setMoved(null); }, []);

  // --- действия ------------------------------------------------------------------------

  const latest = useRef({ ep, groups, unknown, scope, refresh: info.refresh, onLibraryChanged, readOnly: info.newer });
  latest.current = { ep, groups, unknown, scope, refresh: info.refresh, onLibraryChanged, readOnly: info.newer };
  /**
   * После действия — перечитать сразу (и список встреч, если они сменили группу). Событие
   * `groups.changed` перечитает ещё раз; это дёшево, а без явного перечитывания окно отстаёт.
   */
  const changed = useCallback(async (library = false) => {
    const { refresh, onLibraryChanged: lib } = latest.current;
    if (library) lib?.();
    await refresh();
  }, []);

  const [dialog, setDialog] = useState<GroupDialogState | null>(null);
  const [clearAsk, setClearAsk] = useState<ClearAsk | null>(null);

  const create = useCallback((then?: (id: string, name: string) => void) => setDialog({ mode: "create", then }), []);
  const rename = useCallback((id: string) => {
    const g = latest.current.groups.find((x) => x.id === id);
    if (g) setDialog({ mode: "rename", id, name: g.name, color: g.color });
  }, []);
  const nameUnknown = useCallback((id: string) => setDialog({ mode: "name", id }), []);
  const closeDialog = useCallback(() => setDialog(null), []);

  const submitDialog = useCallback(async (name: string, color: string): Promise<string | null> => {
    const { ep: e } = latest.current;
    const d = dialog;
    if (!e || !d) return null;
    let created: { id: string; name: string } | null = null;
    try {
      if (d.mode === "rename") noteMoved(await patchGroup(e, d.id, { name, color }));
      else {
        const got = await createGroup(e, d.mode === "name" ? { id: d.id, name, color } : { name, color });
        noteMoved(got);
        created = { id: got.id, name: got.name || name };
      }
    } catch (cause) {
      return sentence(errorText(cause));
    }
    setDialog(null);
    await changed(d.mode === "name");
    if (d.mode === "create" && created) d.then?.(created.id, created.name);
    return null;
  }, [dialog, changed, noteMoved]);

  const setColor = useCallback(async (id: string, color: string) => {
    const { ep: e } = latest.current;
    if (!e) return;
    try {
      noteMoved(await patchGroup(e, id, { color }));
      await changed();
    } catch (cause) { fail(cause); }
  }, [changed, fail, noteMoved]);

  const setKbFolder = useCallback(async (id: string, folder: string | null, text: string) => {
    const { ep: e } = latest.current;
    if (!e) return;
    try {
      noteMoved(await patchGroup(e, id, { kb_folder: folder }));
      await changed();
      say(text);
    } catch (cause) { fail(cause); }
  }, [changed, fail, noteMoved, say]);

  const pickKbFolder = useCallback(async (id: string) => {
    const { ep: e, groups: list } = latest.current;
    const group = list.find((g) => g.id === id);
    if (!e || !group) return;
    let root: string | null = null;
    try {
      root = (await getAssistant(e)).knowledge_dir ?? null;
    } catch (cause) { fail(cause); return; }
    if (!root) {
      setToast({ n: ++toastN.current, error: true,
        text: "Базы знаний нет — задайте её в настройках ассистента, потом выберите папку группы" });
      return;
    }
    const path = await pickFolder(group.kb_folder ? kbJoin(root, group.kb_folder) : root).catch(() => null);
    if (!path) return;
    const rel = kbRelative(root, path);
    if (rel === null || rel === "") {
      setToast({ n: ++toastN.current, error: true,
        text: rel === "" ? "Это сама база знаний — выберите папку внутри неё"
          : `Папка вне базы знаний — выберите папку внутри ${root}` });
      return;
    }
    await setKbFolder(id, rel, `Папка базы знаний группы «${group.name}»: ${rel}`);
  }, [fail, setKbFolder]);

  const clearKbFolder = useCallback(async (id: string) => {
    const group = latest.current.groups.find((g) => g.id === id);
    if (group) await setKbFolder(id, null, `У группы «${group.name}» больше нет папки базы знаний`);
  }, [setKbFolder]);

  const reorder = useCallback(async (ids: string[]) => {
    const { ep: e, groups: list } = latest.current;
    if (!e || ids.join() === list.map((g) => g.id).join()) return false;
    try {
      noteMoved(await orderGroups(e, ids));
      await changed();
      return true;
    } catch (cause) {
      fail(cause);
      return false;
    }
  }, [changed, fail, noteMoved]);

  const shift = useCallback(async (id: string, delta: -1 | 1) => {
    const ids = latest.current.groups.map((g) => g.id);
    const next = shiftId(ids, id, delta);
    return next === ids ? false : reorder(next);
  }, [reorder]);

  const remove = useCallback(async (id: string) => {
    const { ep: e, scope: was } = latest.current;
    if (!e) return;
    try {
      const got = await deleteGroup(e, id);
      noteMoved(got);
      // Удалили открытую группу — список возвращается ко всем записям; «Отменить» вернёт и её.
      const open = was === id;
      if (open) setScope(null);
      await changed(open);
      // Строки группы (и заголовка) больше нет — фокус на «Все записи», а не в никуда. У группы со
      // встречами строка остаётся («Группа без названия», тот же ключ) — и с неё тоже.
      rescueFocus(allRow, () => focusInRow(id));
      say(`Группа «${got.group.name}» удалена`, async () => {
        // Та же группа с тем же id, местом и всеми полями — в том числе незнакомыми этой версии.
        try {
          noteMoved(await createGroup(e, { ...got.group, index: got.index }));
          await changed(open);
          // Область — после перечитывания: у пустой группы, пока список старый, её нет ни среди
          // групп, ни среди неизвестных, и проверка области сбросила бы её снова.
          if (open) setScope(id);
          focusWhenReady(() => groupRow(id));
        } catch (cause) {
          fail(cause);
          await changed(open);
        }
      });
    } catch (cause) { fail(cause); }
  }, [changed, fail, noteMoved, say, setScope]);

  /** Итог переноса: часть не удалась — сказать честно, иначе — `text` (и «Отменить»). */
  const report = useCallback((got: GroupMembersResult, text: string, undo?: () => Promise<void>, verb = "перенести") => {
    if (got.failed.length) {
      setToast({ n: ++toastN.current, error: true,
        text: `Не удалось ${verb} ${got.failed.length} из ${got.failed.length + got.changed.length}: ${got.failed[0]!.error}` });
    } else if (text) say(text, undo);
  }, [say]);

  const clearUnknown = useCallback((id: string) => {
    setClearAsk({ id, count: latest.current.unknown.find((g) => g.id === id)?.count ?? 0 });
  }, []);
  const answerClear = useCallback((ok: boolean) => {
    const ask = clearAsk;
    setClearAsk(null);
    const { ep: e, scope: was } = latest.current;
    if (!ok || !ask || !e) return;
    void (async () => {
      try {
        // Какие встречи с этим id — спрашиваем у резидента (в списке может быть другая область).
        const { items } = await getRecordings(e, undefined, { groups: [ask.id] });
        const ids = items.filter((r) => r.group === ask.id).map((r) => r.id);
        if (ids.length) report(await setGroupMembers(e, ask.id, { remove: ids }), "Группа убрана из встреч");
        if (was === ask.id) setScope(null);
        await changed(true);
        rescueFocus(allRow);
      } catch (cause) { fail(cause); }
    })();
  }, [clearAsk, changed, fail, report, setScope]);

  const moveMeetings = useCallback(async (ids: string[], prev: Record<string, string | null>, group: string | null,
    known?: string) => {
    const { ep: e } = latest.current;
    const moving = ids.filter((id) => (prev[id] ?? null) !== group);
    if (!e || !moving.length) return;
    const before = Object.fromEntries(moving.map((id) => [id, prev[id] ?? null]));
    const after = Object.fromEntries(moving.map((id) => [id, group]));
    const got = await applyMove(e, before, after);
    const name = !group ? NO_GROUP_NAME
      : latest.current.groups.find((g) => g.id === group)?.name ?? known ?? UNKNOWN_NAME;
    const done = new Set(got.changed);
    const only = (map: Record<string, string | null>) => Object.fromEntries(Object.entries(map).filter(([id]) => done.has(id)));
    report(got, `Перемещено в «${name}»`, done.size ? async () => {
      // Каждая встреча — в свою прежнюю группу (и неизвестную: `restore`); у которой группы не
      // было — из новой убирается. Неудача одной группы не мешает остальным.
      const back = await applyMove(e, only(after), only(before), true);
      report(back, "", undefined, "вернуть");
      await changed(true);
      const first = got.changed.find((id) => back.changed.includes(id));
      if (first) focusWhenReady(() => meetingRow(first));
    } : undefined);
    await changed(true);
    // Встречи ушли из открытой группы (и строка с фокусом с ними) — фокус к поиску списка.
    rescueFocus(() => listSearch() ?? allRow());
  }, [changed, report]);

  // --- перетаскивание -----------------------------------------------------------------

  const onDrop = useRef<(p: DragPayload, t: DropTarget) => void>(() => {});
  onDrop.current = (p, t) => {
    if (p.kind === "meetings" && t.kind === "group") void moveMeetings(p.ids, p.prev, t.group);
    else if (p.kind === "group" && t.kind === "slot" && !latest.current.readOnly) {
      void reorder(reorderIds(latest.current.groups.map((g) => g.id), p.id, t.index));
    }
  };
  const drag = useMemo(() => createGroupDrag({
    onDrop: (p, t) => onDrop.current(p, t),
    order: () => latest.current.groups.map((g) => g.id),
  }), []);
  useEffect(() => () => drag.cancel(), [drag]);

  return {
    shown, supported: info.supported, unavailable, retry, groups, unknown, none, total,
    readOnly: shown && info.newer, broken, dismissBroken,
    scope: shown ? scope : null, setScope, scopeName, scopeCount, libraryScope, nameOf,
    create, rename, nameUnknown, setColor, pickKbFolder, clearKbFolder, shift, reorder, remove, clearUnknown,
    moveMeetings, drag,
    dialog, closeDialog, submitDialog, clearAsk, answerClear, toast, closeToast, holdToast,
  };
}

/**
 * Перенос встреч на резиденте из групп `from` в группы `to` (id встречи →
 * группа, null — без группы): в группу — `add` (прежняя заменяется сама), без
 * группы — `remove` из прежней. Вызов на каждую группу; упавший вызов не мешает
 * остальным — его встречи уходят в `failed`. `restore` — «Отменить»: `add` и в
 * неизвестную группу.
 */
async function applyMove(ep: Endpoint, from: Record<string, string | null>, to: Record<string, string | null>,
  restore = false): Promise<GroupMembersResult> {
  const add = new Map<string, string[]>();
  const remove = new Map<string, string[]>();
  for (const [id, target] of Object.entries(to)) {
    const was = from[id] ?? null;
    if (target === was) continue;
    if (target) add.set(target, [...(add.get(target) ?? []), id]);
    else if (was) remove.set(was, [...(remove.get(was) ?? []), id]);
  }
  const out: GroupMembersResult = { changed: [], failed: [] };
  const calls = [
    ...[...add].map(([gid, list]) => [gid, list, restore ? { add: list, restore: true } : { add: list }] as const),
    ...[...remove].map(([gid, list]) => [gid, list, { remove: list }] as const),
  ];
  for (const [gid, list, change] of calls) {
    try {
      const got = await setGroupMembers(ep, gid, change);
      out.changed.push(...got.changed);
      out.failed.push(...got.failed);
    } catch (cause) {
      const error = errorText(cause);
      out.failed.push(...list.map((id) => ({ id, error })));
    }
  }
  return out;
}
