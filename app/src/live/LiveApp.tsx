/** Заглушка панели ассистента: окно есть, слушание идёт. */
export function LiveApp() {
  return (
    <main className="live-panel" aria-live="polite">
      <span className="live-dot" aria-hidden="true" />
      <span className="live-title">Ассистент слушает…</span>
    </main>
  );
}
