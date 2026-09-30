// Заглушка: настоящий хук — в задаче 2 (см. src/legacy/useResident.ts).
export type ResidentStatus = "connecting" | "online" | "offline";

export function useResident(): { status: ResidentStatus } {
  return { status: "offline" };
}
