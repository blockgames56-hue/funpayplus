import { VpsInstallHint } from "./vps-install";
import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { request, useData } from "./api";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { Button, Confirm, Field, Toggle, Pending, Failure, External } from "./ui";
import type { Telegram } from "./types";

type Status = { configured: boolean; paired: boolean; owner_name: string; username: string; running: boolean; error: string; last_poll: number | null };
export function TelegramSettingsPanel() {
  const query = useData<Telegram>("telegram", "/settings/telegram");
  const status = useData<Status>("telegram-status", "/telegram/status");
  const client = useQueryClient();
  const [form, setForm] = useState<Telegram | null>(null), [pairUrl, setPairUrl] = useState(""), [busy, setBusy] = useState(false);
  useEffect(() => {
    if (!pairUrl) return;
    const tick = setInterval(() => { void client.invalidateQueries({queryKey: ["telegram-status"]}); void client.invalidateQueries({queryKey: ["telegram"]}); }, 2500);
    const expiry = setTimeout(() => setPairUrl(""), 600000);
    return () => { clearInterval(tick); clearTimeout(expiry); };
  }, [pairUrl, client]);
  const baseline = useRef<Telegram | null>(null);
  const queue = useRef<Promise<unknown>>(Promise.resolve());
  useEffect(() => {
    if (!query.data) return;
    const before = baseline.current;
    const incoming = query.data;
    setForm(old => !old || !before ? incoming : Object.fromEntries(Object.entries(incoming).map(([key, value]) => [key, JSON.stringify(old[key as keyof Telegram]) === JSON.stringify(before[key as keyof Telegram]) ? value : old[key as keyof Telegram]])) as unknown as Telegram);
    baseline.current = incoming;
  }, [query.data]);
  function saveDraft(value: Telegram | null) {
    const task = queue.current.catch(() => {}).then(async () => {
      if (!value) return;
      const changed = Object.fromEntries(Object.entries(value).filter(([key, entry]) => key !== "has_token" && JSON.stringify(entry) !== JSON.stringify(baseline.current?.[key as keyof Telegram])));
      if (!Object.keys(changed).length) return;
      const {session_token: _session, ...result} = await request<Telegram & {session_token?: string}>("/settings/telegram", "PATCH", changed);
      baseline.current = result;
      setForm(old => !old ? result : Object.fromEntries(Object.entries(result).map(([key, entry]) => [key, JSON.stringify(old[key as keyof Telegram]) === JSON.stringify(value[key as keyof Telegram]) ? entry : old[key as keyof Telegram]])) as unknown as Telegram);
    });
    queue.current = task;
    return task;
  }
  const save = useAutoSave({ value: form, dirty: !!form && !!baseline.current && JSON.stringify(form) !== JSON.stringify(baseline.current), enabled: !!form,
    save: saveDraft, queryKeys: ["telegram", "telegram-status"] });
  async function connect() {
    setBusy(true); setPairUrl("");
    try {
      await save.flush();
      await saveDraft(form);
      const result = await request<{ url: string }>("/telegram/pair", "POST");
      setPairUrl(result.url);
      setForm(old => old && ({ ...old, control_enabled: true }));
      await client.invalidateQueries({ queryKey: ["telegram-status"] });
      await client.invalidateQueries({ queryKey: ["telegram"] });
      toast.success("Откройте ссылку и нажмите «Запустить» в Telegram");
    } catch (error) { toast.error((error as Error).message); } finally { setBusy(false); }
  }
  async function disconnect() {
    await save.flush(); await saveDraft(form); await request("/telegram/disconnect", "POST"); setPairUrl("");
    setForm(old => old && ({ ...old, control_enabled: false }));
    await client.invalidateQueries({ queryKey: ["telegram-status"] });
    toast.success("Доступ Telegram отозван");
  }
  return <section className="panel settings-panel tg-settings"><div className="panel-head"><h2>✈️ Telegram</h2><span className="tag">Бот + Mini App</span></div><div className="settings-body">{query.isPending ? <Pending /> : query.isError ? <Failure error={query.error} retry={() => void query.refetch()} /> : <>{form && <>
    <details className="tg-notifications"><summary>☁️ Установить на VPS без домена</summary><VpsInstallHint /></details>
    <p className="tg-help">Создайте бота в @BotFather, вставьте токен и привяжите свой Telegram. Chat ID заполнится сам. На VPS используйте простой установщик — он настроит и бота, и Mini App без покупки домена.</p>
    <Field label="Токен бота" hint={form.has_token ? "Токен сохранён. Пустое поле сохраняет текущий." : "Получите токен командой /newbot у @BotFather."}><input type="password" autoComplete="off" value={form.token} onChange={e => setForm({ ...form, token: e.target.value.trim() })} placeholder={form.has_token ? "••••••••••••••••" : "123456:ABC…"}/></Field>
    <div className="setting-row"><div><strong>Управление через Telegram</strong><small>Личный доступ к аккаунтам, лотам, чатам и плагинам</small></div><Toggle label="Управление через Telegram" checked={!!form.control_enabled} onChange={value => setForm({ ...form, control_enabled: value })}/></div>
    <div className="tg-connection"><span className={`tag ${status.data?.paired ? "good" : ""}`}>{status.data?.paired ? `✓ ${status.data.owner_name || "Владелец подключён"}` : "Владелец не привязан"}</span>{status.data?.username && <small>@{status.data.username}</small>}{status.data?.error && <p role="alert">{status.data.error}</p>}{status.data?.paired && !status.data.running && <p>Обработка Telegram сейчас остановлена вместе с фоновыми задачами приложения.</p>}<div className="head-actions"><Button secondary disabled={busy || (!form.has_token && !form.token)} onClick={() => void connect()}>{busy ? "Подключаем…" : status.data?.paired ? "Перепривязать Telegram" : "Подключить мой Telegram"}</Button>{status.data?.paired && <Confirm trigger={<Button secondary>Отключить доступ</Button>} title="Отключить Telegram?" text="Бот и ранее выданные Telegram-сессии потеряют доступ к управлению." onConfirm={disconnect}/>}</div>{pairUrl && <div className="tg-pair-link"><External href={pairUrl}>Открыть бота и привязать владельца</External><small>Одноразовая ссылка действует 10 минут. Не передавайте её другим.</small></div>}</div>
    <Field label="HTTPS-адрес панели для Mini App" hint="Простой установщик заполняет адрес сам: https://IP_СЕРВЕРА. Домен покупать не требуется. Без HTTPS работает меню бота с кнопками."><input value={form.mini_app_url || ""} onChange={e => setForm({ ...form, mini_app_url: e.target.value })} placeholder="https://panel.example.com" type="url"/></Field>
    <details className="tg-notifications"><summary>🔔 Уведомления и ответы</summary><div className="setting-row"><span>Отправлять уведомления</span><Toggle label="Уведомления Telegram" checked={form.enabled} onChange={value => setForm({ ...form, enabled: value })}/></div><div className="checkbox-list">{[["orders", "Новые оплаченные заказы"], ["chats", "Сообщения покупателей"], ["tickets", "Споры и арбитраж"], ["lots", "Поднятие лотов"], ["stock", "Заканчиваются ключи"], ["errors", "Ошибки сессий и прокси"]].map(([value, label]) => <label className="checkbox-field" key={value}><input type="checkbox" checked={form.events.includes(value)} onChange={e => setForm({ ...form, events: e.target.checked ? [...form.events, value] : form.events.filter(item => item !== value) })}/>{label}</label>)}</div><Field label="Получатель уведомлений" hint="При привязке владельца заполняется автоматически. Ответ на уведомление о сообщении отправляется покупателю."><input value={form.chat_id} onChange={e => setForm({ ...form, chat_id: e.target.value })} placeholder="Заполнится после привязки"/></Field><Button secondary disabled={busy} onClick={async () => { try { await save.flush(); await saveDraft(form); await request("/telegram/test", "POST"); toast.success("Тест отправлен"); } catch(e) { toast.error((e as Error).message); } }}>Отправить тест</Button></details>
    <AutoSaveStatus state={save}/><p className="tg-note">На VPS данные сохраняются на VPS. На локальном ПК бот работает, пока запущено приложение. На Linux устанавливается серверная версия проекта, EXE используется на Windows.</p>
  </>}</>}</div></section>;
}
