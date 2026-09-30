// Заглушка: настоящий хук — в задаче 2.
export function useLibrary(): { items: unknown[]; jobs: unknown[]; loading: boolean } {
  return { items: [], jobs: [], loading: false };
}
