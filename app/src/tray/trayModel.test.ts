import type { LiveStatus, Recording, Snapshot } from "../lib/types";
import {
  JUST_STOPPED_MS, folderId, freshStop, isJustStopped, latestSaved, phaseOf, stoppedBetween,
} from "./trayModel";

const snap = (extra: Partial<Snapshot> = {}): Snapshot => ({
  status: "idle", source: null, folder: null, elapsed_s: 0, levels: {},
  auto_record: { enabled: false, processes: [], grace_seconds: 0, state: null, mic: null, render: null },
  recordings_dir: "", gpu_busy: false, disk_free_gb: 100, last_stop: null, ...extra,
} as Snapshot);
const live = (o: Partial<LiveStatus> = {}): LiveStatus => ({
  active: false, starting: false, stopping: false, folder: null, error: null, started_at: null, ...o,
});
const rec = (id: string): Recording => ({
  id, path: id, started_at: null, duration_s: null, tracks: {}, has_transcript: false, has_voices: false,
  title: null, source: "record",
});

test("фаза по снимку", () => {
  expect(phaseOf(null, null).kind).toBe("connecting");
  expect(phaseOf(snap(), false).kind).toBe("offline");
  expect(phaseOf(snap(), true).kind).toBe("idle");
  expect(phaseOf(snap({ status: "recording", source: "auto" }), true))
    .toEqual({ kind: "recording", liveOnly: false, assistant: null, auto: true });
  expect(phaseOf(snap({ status: "recording", live: live({ active: true, attached: true }) }), true))
    .toMatchObject({ kind: "recording", assistant: "on" });
  expect(phaseOf(snap({ live: live({ active: true }) }), true))
    .toMatchObject({ kind: "recording", liveOnly: true });
  expect(phaseOf(snap({ live: live({ starting: true }) }), true).kind).toBe("live-starting");
  expect(phaseOf(snap({ live: live({ active: true, stopping: true }) }), true).kind).toBe("saving");
});

test("id записи — имя папки при любых разделителях", () => {
  expect(folderId("D:\\rec\\2026-10-05_14-05")).toBe("2026-10-05_14-05");
  expect(folderId("/Users/a/Meet/2026-10-05_14-05/")).toBe("2026-10-05_14-05");
  expect(folderId(null)).toBeNull();
});

test("остановка между снимками: обычная запись и запись с ассистентом", () => {
  const recording = snap({ status: "recording", folder: "D:\\rec\\a" });
  expect(stoppedBetween(recording, snap())).toBe("a");
  expect(stoppedBetween(null, snap())).toBeNull();
  expect(stoppedBetween(recording, recording)).toBeNull();
  const listening = snap({ live: live({ active: true, folder: "/rec/b" }) });
  expect(stoppedBetween(listening, snap({ live: live({ folder: "/rec/b" }) }))).toBe("b");
  // Ассистент, выключенный посреди записи, — запись идёт дальше.
  const attached = snap({ status: "recording", folder: "c", live: live({ active: true, attached: true, folder: "c" }) });
  expect(stoppedBetween(attached, snap({ status: "recording", folder: "c" }))).toBeNull();
});

test("свежая остановка по словам резидента — не отмена и не старше десяти минут", () => {
  const at = 1_000_000;
  const stop = (reason: "saved" | "discarded" | "short") => snap({ last_stop: { folder: "D:\\rec\\a", reason, at } });
  expect(freshStop(stop("saved"), at * 1000 + 5_000)).toBe("a");
  expect(freshStop(stop("short"), at * 1000)).toBe("a");
  expect(freshStop(stop("discarded"), at * 1000)).toBeNull();
  expect(freshStop(stop("saved"), at * 1000 + JUST_STOPPED_MS + 1)).toBeNull();
});

test("последняя сохранённая — не та, что пишется сейчас", () => {
  const items = [rec("now"), rec("before")];
  expect(latestSaved(items, snap({ status: "recording", folder: "D:\\rec\\now" }))?.id).toBe("before");
  expect(latestSaved(items, snap())?.id).toBe("now");
  expect(latestSaved([], snap())).toBeNull();
});

test("«только что остановлена» — по резиденту или по тому, что видела панель", () => {
  const now = 5_000_000;
  const a = rec("a");
  expect(isJustStopped(a, snap(), { id: "a", at: now - 1000 }, now)).toBe(true);
  expect(isJustStopped(a, snap(), { id: "a", at: now - JUST_STOPPED_MS - 1 }, now)).toBe(false);
  expect(isJustStopped(a, snap(), { id: "b", at: now }, now)).toBe(false);
  expect(isJustStopped(a, snap({ last_stop: { folder: "a", reason: "saved", at: now / 1000 } }), null, now)).toBe(true);
  expect(isJustStopped(null, snap(), { id: "a", at: now }, now)).toBe(false);
});
