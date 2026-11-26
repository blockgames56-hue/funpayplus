import { telegramMode } from "./telegram";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useSettingsStore } from "./store";
import { serverAddress } from "./server-connection";

export const SERVER_URL_KEY = "funpay-server-url";
export const SERVER_TOKEN_KEY = "funpay-server-token";

function resolveApi(): string {
  if (telegramMode) return "";
  const remote = (localStorage.getItem(SERVER_URL_KEY) || "").trim();
  if (remote) {
    try { return serverAddress(remote); } catch { return remote.replace(/\/+$/, ""); }
  }
  return window.__TAURI_INTERNALS__ ? "http://127.0.0.1:8765" : "";
}

export const API = resolveApi();
let desktopToken = "";
export function setDesktopToken(value: string) {
  desktopToken = value;
}
declare global {
  interface Window {
    __TAURI_INTERNALS__?: unknown;
  }
}

export async function request<T>(
  path: string,
  method = "GET",
  body?: unknown,
): Promise<T> {
  const form = body instanceof FormData;
  const response = await fetch(`${API}/api${path}`, {
    method,
    credentials: "include",
    signal: path === "/auth/session" ? AbortSignal.timeout(10000) : undefined,
    headers: {
      ...(form ? {} : { "Content-Type": "application/json" }),
      ...(desktopToken ? { Authorization: `Bearer ${desktopToken}` } : {}),
    },
    body: body === undefined ? undefined : form ? body : JSON.stringify(body),
  });
  if (!response.ok) {
    if (
      response.status === 401 &&
      path !== "/auth/login" &&
      path !== "/auth/session" &&
      path !== "/auth/connect" &&
      path !== "/telegram/session"
    )
      window.dispatchEvent(new Event("session-expired"));
    // Unified contract: {code, message, details?, correlation_id}; legacy `detail` still works.
    const error = (await response
      .json()
      .catch(() => ({ detail: `HTTP ${response.status}` }))) as {
      code?: string;
      message?: string;
      correlation_id?: string;
      details?: { msg: string; loc: string[] }[];
      detail?: string | { msg: string; loc: string[] }[];
    };
    const fields = error.details ??
      (Array.isArray(error.detail) ? error.detail : undefined);
    let text =
      fields?.map((item) => `${item.loc.slice(1).join(".")}: ${item.msg}`).join("; ") ||
      (typeof error.message === "string" && error.message
        ? error.message
        : typeof error.detail === "string"
          ? error.detail
          : "Ошибка запроса");
    if (error.correlation_id) text += ` · ID ${error.correlation_id}`;
    throw new Error(text);
  }
  const result = await response.json();
  if (path === "/settings/telegram" && typeof result?.session_token === "string") setDesktopToken(result.session_token);
  return result as T;
}

export class AvatarRequestError extends Error {
  constructor(public status: number) {
    super(`Аватарка недоступна: ${status}`);
  }
}

export async function avatarBlob(path: string): Promise<Blob> {
  const response = await fetch(`${API}/api${path}`, {
    credentials: "include",
    headers: desktopToken ? { Authorization: `Bearer ${desktopToken}` } : {},
  });
  if (!response.ok) throw new AvatarRequestError(response.status);
  return response.blob();
}

export function useData<T>(key: string, path: string, enabled = true) {
  return useQuery<T>({
    queryKey: [key, path],
    queryFn: () => request<T>(path),
    enabled,
    refetchInterval: path.startsWith("/settings/") ? false : 30000,
    retry: 1,
  });
}

export function useAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({
      path,
      method = "POST",
      body,
      success,
    }: {
      path: string;
      method?: string;
      body?: unknown;
      success?: string;
    }) => {
      const result = await request<unknown>(path, method, body);
      if (Array.isArray(result)) {
        const errors = (result as { error?: string }[]).filter(
          (row) => row.error,
        );
        if (errors.length)
          throw new Error([...new Set(errors.map((row) => row.error))].join("; "));
      }
      if (success) toast.success(success);
      return result;
    },
    onSettled: () => {
      void client.invalidateQueries();
    },
    onError: (error) => toast.error(error.message),
  });
}

export function sound(kind: "click" | "message" | "order" | "error") {
  if (useSettingsStore.getState().muted) return;
  const notes = {
    click: [800],
    message: [523, 659],
    order: [440, 554, 659],
    error: [220],
  }[kind];
  try {
    const context = new AudioContext();
    notes.forEach((frequency, index) => {
      const oscillator = context.createOscillator();
      const gain = context.createGain();
      const start = context.currentTime + index * 0.1;
      const duration = kind === "click" ? 0.02 : 0.12;
      oscillator.frequency.value = frequency;
      gain.gain.setValueAtTime(0.025, start);
      gain.gain.exponentialRampToValueAtTime(0.001, start + duration);
      oscillator.connect(gain);
      gain.connect(context.destination);
      oscillator.start(start);
      oscillator.stop(start + duration);
    });
    setTimeout(() => {
      void context.close();
    }, 1000);
  } catch {
    /* Audio may be unavailable until the first user gesture. */
  }
}

export function accountQuery(path: string, selected: string) {
  return `${path}${path.includes("?") ? "&" : "?"}${selected ? `account_id=${selected}` : ""}`;
}
export function money(value: string | number = 0, currency = "RUB") {
  return new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value));
}
export function date(value: string | null, withTime = true) {
  if (value && new Date(value).getUTCFullYear() < 1971) return "Дата неизвестна";
  return value
    ? new Date(
        value.endsWith("Z") || /[+-]\d{2}:\d{2}$/.test(value)
          ? value
          : value + "Z",
      ).toLocaleString(
        "ru-RU",
        withTime
          ? {
              day: "2-digit",
              month: "short",
              hour: "2-digit",
              minute: "2-digit",
            }
          : { day: "2-digit", month: "short" },
      )
    : "—";
}
