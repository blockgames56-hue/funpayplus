import { useEffect, useRef, type Dispatch, type SetStateAction } from "react";
import { mergeSettings } from "./merge-settings";
export function useSyncedSettings<T>(incoming: T | undefined, setDraft: Dispatch<SetStateAction<T>>) {
  const previous = useRef<T | undefined>(undefined);
  const signature = JSON.stringify(incoming);
  useEffect(() => {
    if (incoming === undefined || incoming === null) return;
    const before = previous.current;
    previous.current = incoming;
    setDraft(local => mergeSettings(before, incoming, local));
  }, [signature, setDraft]);
}
