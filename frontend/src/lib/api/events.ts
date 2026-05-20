export type JsonEvent<T> = {
  event: string;
  data: T;
};

export type EventHandlers<T> = {
  onEvent: (event: JsonEvent<T>) => void;
  onError?: (error?: unknown) => void;
};

export function openJsonEventSource<T>(url: string, handlers: EventHandlers<T>): EventSource {
  const source = new EventSource(url);

  function handleJsonEvent(eventName: string, event: Event) {
    let data: T;
    try {
      data = JSON.parse((event as MessageEvent).data) as T;
    } catch (error) {
      source.close();
      handlers.onError?.(error);
      return;
    }
    handlers.onEvent({ event: eventName, data });
  }

  source.addEventListener('job', (event) => handleJsonEvent('job', event));
  source.addEventListener('jobs', (event) => handleJsonEvent('jobs', event));

  source.onerror = () => {
    handlers.onError?.();
  };

  return source;
}
