import { create } from 'zustand';
import { persist } from 'zustand/middleware';

export const useAccountStore = create<{ selected: string; select: (id: string) => void }>(
  (set) => ({ selected: '', select: (selected) => set({ selected }) }),
);
export const useChatStore = create<{
  selected: number | null;
  select: (id: number | null) => void;
}>((set) => ({ selected: null, select: (selected) => set({ selected }) }));
export const useLotStore = create<{
  selection: Record<string, boolean>;
  setSelection: (value: Record<string, boolean>) => void;
}>((set) => ({ selection: {}, setSelection: (selection) => set({ selection }) }));
export const usePluginStore = create<{
  selected: string | null;
  select: (id: string | null) => void;
}>((set) => ({ selected: null, select: (selected) => set({ selected }) }));
export const useSettingsStore = create(
  persist<{
    muted: boolean;
    compact: boolean;
    collapsed: boolean;
    toggle: (key: 'muted' | 'compact' | 'collapsed') => void;
  }>(
    (set) => ({
      muted: false,
      compact: false,
      collapsed: false,
      toggle: (key) => set((state) => ({ [key]: !state[key] })),
    }),
    { name: 'funpay-plus-ui' },
  ),
);
