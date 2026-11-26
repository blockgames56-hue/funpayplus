/** Adopt remote fields while retaining only edits made locally since the last snapshot. */
export function mergeSettings<T>(before: T | undefined | null, incoming: T, local: T): T {
  if (local == null || before == null || JSON.stringify(local) === JSON.stringify(before)) return incoming;
  if (JSON.stringify(incoming) === JSON.stringify(before)) return local;
  if (typeof incoming !== "object" || incoming === null || Array.isArray(incoming) || typeof local !== "object" || Array.isArray(local)) return local;
  const old = before as Record<string, unknown>, next = incoming as Record<string, unknown>, draft = local as Record<string, unknown>;
  return Object.fromEntries(Object.keys({...next, ...draft}).map(key => [key, mergeSettings(old[key], next[key], draft[key])])) as T;
}
