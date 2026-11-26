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
  onApproval?: (url: string) => void,
  signal?: AbortSignal,
): Promise<string> {
  const controller = new AbortController();
  const cancel = () => controller.abort();
  signal?.addEventListener("abort", cancel);
  if (signal?.aborted) controller.abort();
  let timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    let response = await fetch(`${base}/api/auth/connect`, {
      method: "POST",
      credentials: "omit",
      redirect: "error",
      signal: controller.signal,
      headers: { "Content-Type": "application/json" },
      body: "{}",
    });
    if (response.status === 403 && onApproval) {
      const deviceResponse = await fetch(`${base}/api/telegram/device`, { method: "POST", credentials: "omit", redirect: "error", signal: controller.signal, headers: {"Content-Type": "application/json"}, body: "{}" });
      if (deviceResponse.ok) {
        const device = await deviceResponse.json() as {device: string; secret: string; url: string};
        if (!/^https:\/\/t\.me\/[A-Za-z0-9_]+\?start=login_[A-Za-z0-9_-]+$/.test(device.url)) throw new Error("Сервер вернул неверный адрес подтверждения");
        onApproval(device.url);
        clearTimeout(timer); timer = setTimeout(() => controller.abort(), 300000);
        while (!controller.signal.aborted) {
          await new Promise(resolve => setTimeout(resolve, 2000));
          const poll = await fetch(`${base}/api/telegram/device/poll`, {method: "POST", credentials: "omit", redirect: "error", signal: controller.signal, headers: {"Content-Type": "application/json"}, body: JSON.stringify({device: device.device, secret: device.secret})});
          if (!poll.ok) throw new Error("Подтверждение истекло. Повторите подключение.");
          const result = await poll.json() as {pending?: boolean; session_token?: string};
          if (result.session_token) { response = new Response(JSON.stringify({ok: true, session_token: result.session_token}), {status: 200, headers: {"Content-Type":"application/json"}}); break; }
        }
      }
    }
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
    signal?.removeEventListener("abort", cancel);
  }
}
