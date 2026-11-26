export type LiveEvent = { channel: string; data: { source_plugin?: string; level?: string; event?: string; text?: string } };
/** A broken frame must not interrupt subsequent events or crash the interface. */
export function parseLiveEvent(raw: string): LiveEvent | null {
  try {
    const value = JSON.parse(raw);
    if (!value || typeof value !== "object" || Array.isArray(value) || typeof value.channel !== "string" || !value.data || typeof value.data !== "object" || Array.isArray(value.data)) return null;
    for (const key of ["source_plugin", "level", "event", "text"]) if (value.data[key] !== undefined && typeof value.data[key] !== "string") return null;
    return value;
  } catch { return null; }
}

const paths: Record<string, string[]> = {
  accounts: ["/accounts", "/dashboard", "/health"],
  chats: ["/chats", "/messages", "/dashboard"],
  orders: [
    "/orders",
    "/dashboard",
    "/plugins/integrated/",
    "/plugins/kosell",
    "/plugins/fulfillment",
    "/loyalty",
  ],
  lots: ["/lots", "/auto-dump/", "/dashboard", "/plugins/kosell"],
  tickets: ["/tickets", "/plugins/integrated/support-tickets", "/logs"],
  logs: ["/logs", "/activities", "/health"],
  loyalty: ["/loyalty", "/dashboard"],
};
export function matchesLiveQuery(
  key: readonly unknown[],
  events: readonly LiveEvent[],
): boolean {
  return events.some((event) => {
    if (key[0] === "plugin-logs")
      return (
        event.channel === "logs" &&
        (!event.data.source_plugin || event.data.source_plugin === key[1])
      );
    if (key[0] === "messages") return event.channel === "chats";
    return (
      typeof key[1] === "string" &&
      (Object.hasOwn(paths, event.channel) ? paths[event.channel] : []).some((prefix) =>
        (key[1] as string).startsWith(prefix),
      )
    );
  });
}
/** Fixed windows merge event bursts without postponing refresh during continuous traffic. */
export function createLiveRefresh(
  refresh: (events: LiveEvent[]) => void,
  interval = 250,
) {
  const pending = new Map<string, LiveEvent>();
  let timer: ReturnType<typeof setTimeout> | undefined;
  let disposed = false;
  return {
    push(event: LiveEvent) {
      if (disposed || !Object.hasOwn(paths, event.channel)) return;
      pending.set(`${event.channel}:${event.data.source_plugin || ""}`, event);
      if (timer !== undefined) return;
      timer = setTimeout(() => {
        timer = undefined;
        const events = [...pending.values()];
        pending.clear();
        if (!disposed && events.length) refresh(events);
      }, interval);
    },
    dispose() {
      disposed = true;
      if (timer !== undefined) clearTimeout(timer);
      timer = undefined;
      pending.clear();
    },
  };
}
