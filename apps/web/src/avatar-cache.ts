import { avatarBlob } from "./api";

interface Entry {
  blob: Blob;
  savedAt: number;
}
const age = 7 * 86400 * 1000;
let database: Promise<IDBDatabase> | null = null;
const pending = new Map<string, Promise<Blob>>();

export function avatarSource(value?: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value, "https://funpay.com/");
    if (
      url.protocol !== "https:" ||
      url.username ||
      url.password ||
      (url.port && url.port !== "443") ||
      !["funpay.com", "sfunpay.com"].some(
        (host) => url.hostname === host || url.hostname.endsWith("." + host),
      ) ||
      url.pathname === "/img/layout/avatar.png"
    )
      return null;
    url.hash = "";
    return url.href;
  } catch {
    return null;
  }
}

function openDatabase(): Promise<IDBDatabase> {
  if (!database)
    database = new Promise((resolve, reject) => {
      const request = indexedDB.open("funpay-plus-avatars", 1);
      request.onupgradeneeded = () =>
        request.result.createObjectStore("images");
      request.onsuccess = () => {
        request.result.onversionchange = () => {
          request.result.close();
          database = null;
        };
        resolve(request.result);
      };
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("Кэш аватарок занят"));
    });
  return database;
}

async function read(key: string): Promise<Blob | null> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const request = db
      .transaction("images", "readonly")
      .objectStore("images")
      .get(key);
    request.onsuccess = () => {
      const value = request.result as Entry | undefined;
      resolve(value && Date.now() - value.savedAt < age ? value.blob : null);
    };
    request.onerror = () => reject(request.error);
  });
}

async function write(key: string, blob: Blob): Promise<void> {
  const db = await openDatabase();
  return new Promise((resolve, reject) => {
    const transaction = db.transaction("images", "readwrite");
    transaction
      .objectStore("images")
      .put({ blob, savedAt: Date.now() } satisfies Entry, key);
    transaction.oncomplete = () => resolve();
    transaction.onerror = () => reject(transaction.error);
  });
}

async function loadAvatar(key: string, path: string): Promise<Blob> {
  let existing: Blob | null = null;
  let timeout: ReturnType<typeof setTimeout> | undefined;
  try {
    existing = await Promise.race([
      read(key),
      new Promise<null>((resolve) => {
        timeout = setTimeout(() => resolve(null), 600);
      }),
    ]);
  } catch {
    /* Storage can be disabled by the browser. */
  } finally {
    clearTimeout(timeout);
  }
  if (existing) return existing;
  // The same account/chat endpoint can serve a new photo after a profile update.
  const blob = await avatarBlob(
    `${path}${path.includes("?") ? "&" : "?"}v=${encodeURIComponent(key)}`,
  );
  if (!blob.type.startsWith("image/")) throw new Error("Некорректная аватарка");
  void write(key, blob).catch(() => {
    /* The photo does not wait for IndexedDB. */
  });
  return blob;
}

export function cachedAvatar(key: string, path: string): Promise<Blob> {
  const id = `${path}|${key}`;
  if (!pending.has(id)) {
    const request = loadAvatar(id, path).finally(() => pending.delete(id));
    pending.set(id, request);
  }
  return pending.get(id)!;
}
