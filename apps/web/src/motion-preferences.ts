import { useSyncExternalStore } from "react";
const query =
  typeof window !== "undefined"
    ? window.matchMedia("(prefers-reduced-motion: reduce)")
    : null;
const subscribe = (notify: () => void) => {
  query?.addEventListener("change", notify);
  return () => query?.removeEventListener("change", notify);
};
export function usePrefersReducedMotion() {
  return useSyncExternalStore(
    subscribe,
    () => query?.matches ?? false,
    () => false,
  );
}
