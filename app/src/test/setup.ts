import "@testing-library/jest-dom/vitest";
import { configure } from "@testing-library/react";

// findBy*/waitFor ждут появления до 5 с, а не 1 с по умолчанию: на 4-ядерном
// CI-раннере асинхронный интерфейс появляется дольше секунды, и тесты падали
// по очереди то один, то другой. Зелёный тест не ждёт — проверка срабатывает сразу.
export const ASYNC_TIMEOUT_MS = 5_000;
configure({ asyncUtilTimeout: ASYNC_TIMEOUT_MS });

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
  // Флаг участника, запомненный окном (`LiveWorkspace.PARTICIPANT_KEY`): прошлый тест
  // файла не выбирает раскладку следующему до первого состояния.
  try { localStorage.removeItem("meet.live.participant"); } catch { /* нет хранилища */ }
});
// Сеансы вкладки «Агент» живут вне React (features/card/agentSessions): каждый
// тест начинает без сеансов и подписок прошлого. Модуль сам кладёт сюда сброс
// (импорт отсюда загрузил бы lib/shell раньше, чем тест подменит его vi.mock).
afterEach(() => {
  (globalThis as { __resetAgentSessions?: () => void }).__resetAgentSessions?.();
});
