export type AppEvent = 'NEW_MESSAGE' | 'ORDER_PAID' | 'SYSTEM_LOG' | 'ERROR';
type Listener = (payload: unknown) => void;

class EventBus {
  private listeners = new Map<AppEvent, Set<Listener>>();

  on(event: AppEvent, listener: Listener): () => void {
    const listeners = this.listeners.get(event) ?? new Set<Listener>();
    listeners.add(listener);
    this.listeners.set(event, listeners);
    return () => listeners.delete(listener);
  }

  emit(event: AppEvent, payload: unknown): void {
    this.listeners.get(event)?.forEach((listener) => listener(payload));
  }
}

export const eventBus = new EventBus();
