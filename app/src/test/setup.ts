import "@testing-library/jest-dom/vitest";

// jsdom не знает EventSource: минимальная заглушка, которой тесты могут управлять.
export class FakeEventSource {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSED = 2;
  static instances: FakeEventSource[] = [];
  onmessage: ((e: MessageEvent) => void) | null = null;
  onerror: (() => void) | null = null;
  closed = false;
  readyState = FakeEventSource.OPEN;
  private listeners = new Map<string, Array<(e: MessageEvent) => void>>();
  constructor(public url: string) {
    FakeEventSource.instances.push(this);
  }
  addEventListener(kind: string, fn: (e: MessageEvent) => void) {
    this.listeners.set(kind, [...(this.listeners.get(kind) ?? []), fn]);
  }
  close() {
    this.closed = true;
    this.readyState = FakeEventSource.CLOSED;
  }
  /** Тестовый хелпер: доставить именованное событие; `id` — его `id:` (lastEventId). */
  emit(kind: string, data: unknown, id?: string | number) {
    const e = { data: JSON.stringify(data), lastEventId: id === undefined ? "" : String(id) } as MessageEvent;
    for (const fn of this.listeners.get(kind) ?? []) fn(e);
  }
  /**
   * Тестовый хелпер: обрыв. `closed` — браузер сдался (не 200, например 409)
   * и сам переподключаться не будет; иначе он переподключается сам.
   */
  fail(closed: boolean) {
    this.readyState = closed ? FakeEventSource.CLOSED : FakeEventSource.CONNECTING;
    this.onerror?.();
  }
}
(globalThis as any).EventSource = FakeEventSource;
beforeEach(() => {
  FakeEventSource.instances = [];
});
