export function serverAddress(value: string): string {
  let url: URL;
  try {
    url = new URL(value.trim());
  } catch {
    throw new Error(
      "Укажите полный адрес: https://panel.example.com или http://127.0.0.1:8080",
    );
  }
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password ||
    url.search ||
    url.hash ||
    !["", "/", "/api", "/api/"].includes(url.pathname)
  ) {
    throw new Error(
      "Укажите адрес панели без логина, пароля, параметров и пути к разделу.",
    );
  }
  return url.origin;
}

export async function checkServer(
  base: string,
  timeoutMs = 10000,
): Promise<string> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${base}/api/auth/connect`, {
      method: "POST",
      credentials: "omit",
      redirect: "error",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (!response.ok)
      throw new Error(
        response.status === 404
          ? "Обновите приложение на VPS: сервер использует старую версию входа."
          : response.status === 403
            ? "Сервер отклонил подключение. Проверьте адрес панели и разрешённые источники."
            : `Сервер ответил ошибкой ${response.status}`,
      );
    const session = (await response.json().catch(() => null)) as {
      ok?: boolean;
      session_token?: string;
    } | null;
    if (
      !session?.ok ||
      typeof session.session_token !== "string" ||
      !session.session_token.includes(".")
    ) {
      throw new Error(
        "По этому адресу не найдена панель FunPay Plus. Проверьте адрес сервера.",
      );
    }
    const health = await fetch(`${base}/api/health`, {
      headers: { Authorization: `Bearer ${session.session_token}` },
      credentials: "omit",
      redirect: "error",
      signal: controller.signal,
    });
    const info = (await health.json().catch(() => null)) as {
      ok?: boolean;
      database?: string;
    } | null;
    if (
      !health.ok ||
      !info?.ok ||
      !["postgresql", "sqlite-wal"].includes(info.database || "")
    )
      throw new Error(
        "Сервер не подтвердил готовность приложения. Проверьте его состояние.",
      );
    return session.session_token;
  } catch (error) {
    if (controller.signal.aborted)
      throw new Error(
        "Сервер не ответил вовремя. Проверьте адрес и доступность VPS.",
      );
    if (error instanceof TypeError)
      throw new Error(
        "Не удалось подключиться. Проверьте сеть, сертификат и запуск сервера.",
      );
    throw error;
  } finally {
    clearTimeout(timer);
  }
}
