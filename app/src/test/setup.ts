import "@testing-library/jest-dom/vitest";

// jsdom не знает EventSource: минимальная заглушка, которой тесты могут управлять.
export class FakeEventSource {
  static instances: FakeEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  private listeners = new Map<string, Array<(e: MessageEvent) => void>>();
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(kind: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(kind, [...(this.listeners.get(kind) ?? []), fn]);
  }
  close() {
    this.closed = true;
  }
  /** Тестовый хелпер: доставить именованное событие. */
  emit(kind: string, data: unknown) {
    const e = { data: JSON.stringify(data) } as MessageEvent;
    for (const fn of this.listeners.get(kind) ?? []) fn(e);
  }
}
(globalThis as any).EventSource = FakeEventSource;
beforeEach(() => {
  FakeEventSource.instances = [];
});
