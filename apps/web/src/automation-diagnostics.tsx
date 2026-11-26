import { useAction, useData } from "./api";
import { Button, Modal } from "./ui";
import { useState } from "react";

interface Diagnostics {
  accounts: { id: number; username: string; state: string; last_poll?: string; last_error?: string }[];
  events: Record<string, number>;
  unknown_sends: number;
  recent: { id: number; event_key: string; state: string; reason?: string }[];
  responder: {
    enabled: boolean;
    dry_run: boolean;
    account_scope: "all" | "selected";
    account_ids: number[];
    greeting_enabled: boolean;
    commands: number;
    keywords: number;
  };
}

const ignoredLabels: Record<string, string> = {
  responder_disabled: "Автоответчик остановлен",
  initial_or_history: "История до запуска пропущена",
  own_message: "Собственное сообщение пропущено",
  missing_identity: "Нет надёжного ID отправителя",
  blacklist: "Покупатель в чёрном списке",
  account_inactive_or_message_missing: "Аккаунт неактивен или сообщение удалено",
};

export function AutomationDiagnostics() {
  const query = useData<Diagnostics>("automation-diagnostics", "/automation/status");
  const deliveries = useData<{ id: number; order_id: number; state: string }[]>("delivery-reservations", "/automation/deliveries");
  const action = useAction();
  const [resolution, setResolution] = useState<{ id: number; action: "confirm_sent" | "release" } | null>(null);
  const [note, setNote] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  function selectResolution(id: number, choice: "confirm_sent" | "release") {
    setNote(""); setConfirmed(false); setResolution({ id, action: choice });
  }
  if (query.isPending) return <p>Загрузка диагностики…</p>;
  if (query.isError) return <p role="alert">Не удалось загрузить диагностику.</p>;
  const data = query.data;
  return <section className="ws-panel ws-pad ws-form">
    <strong>Диагностика обработки сообщений</strong>
    {data.accounts.map(a => <div className="ws-row" key={a.id}>
      <span>{a.username || `Аккаунт #${a.id}`}</span>
      <span>{a.state} · {a.last_poll ? new Date(a.last_poll).toLocaleString() : "Опрос ещё не выполнен"}{a.last_error ? ` · ${a.last_error}` : ""}</span>
    </div>)}
    {!data.responder.enabled && (
      <p role="alert">Автоответчик остановлен: новые сообщения не получат автоматический ответ.</p>
    )}
    {data.responder.enabled && !data.responder.greeting_enabled && !data.responder.commands && !data.responder.keywords && (
      <p role="alert">Нет активного приветствия, команд или ключевых фраз — отвечать на обычные сообщения нечем.</p>
    )}
    {data.responder.dry_run && <p>Тестовый режим: решения записываются без отправки в FunPay.</p>}
    <p>{Object.entries(data.events).map(([state, count]) => `${state}: ${count}`).join(" · ") || "Событий пока нет"}</p>
    {data.recent.filter(e => e.state === "IGNORED" && e.reason).slice(0, 3).map(e => (
      <p className="muted" key={e.id}>Событие #{e.id}: {ignoredLabels[e.reason!] || e.reason}</p>
    ))}
    {data.unknown_sends > 0 && <p role="alert">Отправок с неизвестным результатом: {data.unknown_sends}. Проверьте историю на FunPay перед повтором.</p>}
    {data.recent.filter(e => e.state === "FAILED").map(e => <div className="ws-row" key={e.id}>
      <span>#{e.id}: {e.reason}</span>
      <Button secondary disabled={action.isPending} onClick={() => action.mutate({ path: `/automation/events/${e.id}/retry` })}>Повторить обработку</Button>
    </div>)}
    <strong>Журнал резервирования товаров</strong>
    {deliveries.isError && <p role="alert">Не удалось загрузить журнал выдачи.</p>}
    {deliveries.isPending && <p>Загрузка журнала…</p>}
    {deliveries.data?.length === 0 && <p>Новых резервирований пока нет.</p>}
    {deliveries.data?.slice(0, 10).map(row => <div className="ws-row" key={row.id}>
      <span>Заказ #{row.order_id} · резерв #{row.id}</span>
      <span>{({ reserved: "Зарезервирован", sent: "Выдан", uncertain: "Требует сверки с чатом FunPay", released: "Возвращён в пул, автовыдача отменена" } as Record<string, string>)[row.state] || row.state}</span>
      {row.state === "uncertain" && <div>
        <Button secondary disabled={action.isPending} onClick={() => selectResolution(row.id, "confirm_sent")}>Выдано — подтвердить</Button>
        <Button secondary disabled={action.isPending} onClick={() => selectResolution(row.id, "release")}>Не отправлено — вернуть в пул</Button>
      </div>}
    </div>)}
    <Modal title="Сверка выдачи" description={resolution?.action === "release" ? "Ключ вернётся в пул. Автовыдача этого заказа будет отменена." : "Заказ будет отмечен выданным. Новое сообщение отправляться не будет."}
      open={resolution !== null} onOpenChange={open => { if (!open && !action.isPending) setResolution(null); }}>
      <textarea aria-label="Результат проверки чата" placeholder="Опишите результат проверки чата (от 10 символов)" value={note} maxLength={1000} onChange={e => setNote(e.target.value)} />
      <label><input type="checkbox" checked={confirmed} onChange={e => setConfirmed(e.target.checked)} />Я проверил историю чата FunPay и подтверждаю выбранный результат</label>
      <Button disabled={!confirmed || note.trim().length < 10 || action.isPending} onClick={() => {
        if (resolution) action.mutate({ path: `/automation/deliveries/${resolution.id}/resolve`, body: { action: resolution.action, confirmed, note }, success: "Результат сверки сохранён" }, { onSuccess: () => setResolution(null) });
      }}>Сохранить результат сверки</Button>
    </Modal>
  </section>;
}
