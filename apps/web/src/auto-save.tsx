import { toast } from "sonner";
import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Button } from "./ui";

type Status = "idle" | "waiting" | "saving" | "saved" | "error";
type Options<T> = {
  value: T;
  dirty: boolean;
  enabled?: boolean;
  save: (value: T) => Promise<unknown>;
  onSaved?: (value: T) => void;
  queryKeys?: string[];
  delay?: number;
};

/** Debounced, serialized writes. A slow response never discards newer edits. */
export function useAutoSave<T>(options: Options<T>) {
  const client = useQueryClient();
  const signature = JSON.stringify(options.value);
  const latest = useRef({ ...options, signature });
  latest.current = { ...options, signature };
  const active = useRef(false), mounted = useRef(true), acknowledged = useRef<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [status, setStatus] = useState<Status>("idle");
  const [error, setError] = useState("");
  const pump = useCallback(async () => {
    if (active.current) return;
    const current = latest.current;
    if (!current.dirty || current.enabled === false || current.signature === acknowledged.current) return;
    active.current = true;
    if (mounted.current) { setStatus("saving"); setError(""); }
    let failed = false;
    try {
      const snapshot = JSON.parse(current.signature) as T;
      await current.save(snapshot);
      acknowledged.current = current.signature;
      for (const key of current.queryKeys || []) void client.invalidateQueries({ queryKey: [key] });
      if (latest.current.signature === current.signature) {
        if (mounted.current) { setStatus("saved"); current.onSaved?.(snapshot); }
      }
    } catch (reason) {
      failed = true;
      if (mounted.current) { setStatus("error"); setError(reason instanceof Error ? reason.message : "Не удалось применить изменения"); }
      else toast.error("Не удалось применить последние изменения: " + (reason instanceof Error ? reason.message : "ошибка соединения"));
    } finally {
      active.current = false;
      const next = latest.current;
      if ((!failed || next.signature !== current.signature) && next.dirty && next.enabled !== false && next.signature !== acknowledged.current) {
        timer.current = setTimeout(() => void pump(), mounted.current ? (next.delay ?? 600) : 0);
      }
    }
  }, [client]);
  useEffect(() => {
    if (timer.current) clearTimeout(timer.current);
    if (!options.dirty || options.enabled === false) return;
    if (signature === acknowledged.current) { setStatus("saved"); options.onSaved?.(options.value); return; }
    setStatus(active.current ? "saving" : "waiting");
    timer.current = setTimeout(() => void pump(), options.delay ?? 600);
    return () => { if (timer.current) clearTimeout(timer.current); };
  }, [signature, options.dirty, options.enabled, options.delay, pump]);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current) clearTimeout(timer.current);
      // Leaving a tab must not lose the final keystroke before the debounce.
      void pump();
    };
  }, [pump]);
  return { status, error, flush: pump, isSaving: status === "waiting" || status === "saving" };
}

export function AutoSaveStatus({ state }: { state: ReturnType<typeof useAutoSave> }) {
  return <div className={`auto-save-status ${state.error ? "failed" : ""}`} role="status" aria-live="polite">
    {state.error ? <><span>{state.error}</span><Button secondary onClick={() => void state.flush()}>Повторить</Button></> : <span>{state.status === "saving" ? "Применяем…" : state.status === "waiting" ? "Есть изменения…" : state.status === "saved" ? "✓ Применено" : "Изменения применяются автоматически"}</span>}
  </div>;
}
