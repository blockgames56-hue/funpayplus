export function serverAddress(value: string): string {
  let url: URL;
  try { url = new URL(value.trim()); } catch {
    throw new Error("Укажите полный адрес: https://panel.example.com или http://127.0.0.1:8080");
  }
  if (!["https:", "http:"].includes(url.protocol) || url.username || url.password ||
      url.search || url.hash || !["", "/", "/api", "/api/"].includes(url.pathname)) {
    throw new Error("Укажите адрес панели без логина, пароля, параметров и пути к разделу.");
  }
  return url.origin;
}

export async function checkServer(base: string, token: string, timeoutMs = 10000): Promise<void> {
  if (token.trim().length < 24) throw new Error("Введите API_TOKEN вашего сервера (не менее 24 символов).");
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${base}/api/health`, {
      headers: { Authorization: `Bearer ${token.trim()}` },
      credentials: "omit", redirect: "error", signal: controller.signal,
    });
    if (!response.ok) throw new Error(response.status === 401
      ? "Сервер доступен, но ключ не подходит. Введите API_TOKEN из .env сервера."
      : response.status === 403 ? "Сервер отклонил подключение. Проверьте ALLOWED_ORIGINS и адрес панели."
      : `Сервер ответил ошибкой ${response.status}`);
    const health = await response.json().catch(() => null) as { ok?: boolean; database?: string } | null;
    if (!health || health.ok !== true || !["postgresql", "sqlite-wal"].includes(health.database || "")) {
      throw new Error("По этому адресу не найдена панель FunPay Plus. Проверьте адрес и настройку reverse proxy.");
    }
  } catch (error) {
    if (controller.signal.aborted) throw new Error("Сервер не ответил за 10 секунд. Проверьте адрес и доступность VPS.");
    if (error instanceof TypeError) throw new Error("Не удалось подключиться. Проверьте HTTPS-сертификат, сеть, CORS и запуск сервера.");
    throw error;
  } finally { clearTimeout(timer); }
}
