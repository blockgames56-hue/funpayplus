/** Telegram mode never inherits a desktop client's saved server address. */
export const telegramMode = location.pathname.startsWith("/telegram") || sessionStorage.getItem("funpay-telegram-mode") === location.origin;
if (telegramMode) { sessionStorage.setItem("funpay-telegram-mode", location.origin); document.documentElement.classList.add("telegram-mode"); }

type WebApp = { initData: string; ready(): void; expand(): void; close(): void; BackButton: { show(): void; hide(): void; onClick(fn: () => void): void; offClick(fn: () => void): void } };
declare global { interface Window { Telegram?: { WebApp?: WebApp } } }
let sdk: Promise<WebApp> | undefined;
export function telegramApp(): Promise<WebApp> {
  if (window.Telegram?.WebApp) return Promise.resolve(window.Telegram.WebApp);
  return sdk ||= new Promise((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://telegram.org/js/telegram-web-app.js";
    script.async = true;
    const timer = setTimeout(() => { sdk = undefined; reject(new Error("Telegram не ответил. Откройте панель заново из бота.")); }, 12000);
    script.onload = () => { clearTimeout(timer); if (window.Telegram?.WebApp) resolve(window.Telegram.WebApp); else { sdk = undefined; reject(new Error("Откройте панель из Telegram-бота")); } };
    script.onerror = () => { clearTimeout(timer); sdk = undefined; reject(new Error("Не удалось загрузить Telegram. Проверьте соединение.")); };
    document.head.append(script);
  });
}
