import type { Job, Recording, Snapshot } from "./types";

export type RecStatus =
  | { kind: "recording" }
  | { kind: "queued" }
  | { kind: "running"; stage: string; label: string; done?: number; total?: number }
  | { kind: "failed"; error: string; retry: "transcribe" | "import" }
  | { kind: "ready" }
  | { kind: "untranscribed" }
  | { kind: "importing" };

const STAGES: Record<string, string> = {
  convert: "Конвертация",
  asr: "Распознавание",
  align: "Выравнивание",
  diarize: "Спикеры",
  voices: "Голоса",
  render: "Сохранение",
  copy: "Копирование",
};

const norm = (p: string) => p.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();

export function statusOf(rec: Recording, jobs: Job[], snapshot: Snapshot | null): RecStatus {
  const mine = jobs.filter((j) => norm(j.folder) === norm(rec.path));
  const noTracks = Object.keys(rec.tracks ?? {}).length === 0;
  const isImport = rec.source === "import" && noTracks;

  if (snapshot?.status === "recording" && snapshot.folder && norm(snapshot.folder) === norm(rec.path))
    return { kind: "recording" };
  if (mine.some((j) => j.state === "queued")) return { kind: "queued" };
  const running = mine.find((j) => j.state === "running");
  if (running) {
    const stage = running.stage ?? "";
    const out: RecStatus = {
      kind: "running",
      stage,
      label: STAGES[stage] ?? running.label ?? stage,
    };
    if (running.done !== null) out.done = running.done;
    if (running.total !== null) out.total = running.total;
    return out;
  }
  const last = mine[mine.length - 1];
  if (last?.state === "failed" && !rec.has_transcript)
    return { kind: "failed", error: last.error ?? "", retry: isImport ? "import" : "transcribe" };
  if (rec.has_transcript) return { kind: "ready" };
  if (isImport) return { kind: "importing" };
  return { kind: "untranscribed" };
}
