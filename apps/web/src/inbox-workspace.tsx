import { useState } from "react";
import { Link } from "react-router-dom";
import { RefreshCw, Bell, Package, ShoppingCart } from "lucide-react";
import { accountQuery, date, money, useData } from "./api";
import { Button, Empty, Failure, Pending, Status } from "./ui";
import { InboxPage } from "./pages";
import { LotsPage } from "./lots-page";
import { useAccountStore } from "./store";
import type { Activity, Order } from "./types";
import "./workspaces.css";
export function ActivityWorkspace({
  accountId,
  chatOnly = false,
}: {
  accountId?: number;
  chatOnly?: boolean;
}) {
  const query = useData<Activity[]>(
    "workspace-activity",
    accountQuery("/logs", accountId ? String(accountId) : ""),
  );
  const [search, setSearch] = useState("");
  const rows = (query.data || []).filter(
    (r) =>
      (!chatOnly || ["chats", "orders", "system"].includes(r.channel)) &&
      r.text.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <section className="ws-panel">
      <div className="ws-toolbar">
        <input
          aria-label="Поиск событий"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Найти событие"
        />
        <Button
          secondary
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
        >
          <RefreshCw size={14} />
          Обновить
        </Button>
        <small>Последние 200 событий</small>
      </div>
      {query.isPending ? (
        <Pending />
      ) : query.isError ? (
        <Failure error={query.error} retry={() => void query.refetch()} />
      ) : (
        <div className="ws-log ws-table-scroll">
          <table className="ws-table">
            <thead>
              <tr>
                <th>Дата</th>
                <th>Тип</th>
                <th>Аккаунт</th>
                <th>Событие</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>{date(r.created_at)}</td>
                  <td>
                    <span
                      className={`ws-badge ${r.level === "error" ? "bad" : ""}`}
                    >
                      {r.channel}
                    </span>
                  </td>
                  <td>{r.account_id ? `#${r.account_id}` : "Система"}</td>
                  <td style={{ whiteSpace: "normal", minWidth: 220 }}>
                    {r.text}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && <div className="ws-empty">Событий пока нет</div>}
        </div>
      )}
    </section>
  );
}
export function OrdersWorkspace({ accountId }: { accountId?: number }) {
  const query = useData<(Order & { date_source?: string })[]>(
    "workspace-orders",
    accountQuery("/orders", accountId ? String(accountId) : ""),
  );
  const [status, setStatus] = useState("paid"),
    [search, setSearch] = useState("");
  const rows = (query.data || []).filter(
    (o) =>
      o.status === status &&
      `${o.funpay_order_id} ${o.buyer_name} ${o.description}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <section className="ws-panel">
      <div className="ws-toolbar">
        <div className="segmented">
          {[
            ["paid", "Оплачено"],
            ["closed", "Закрыто"],
            ["refunded", "Возвраты"],
          ].map(([id, label]) => (
            <button
              key={id}
              className={id === status ? "active" : ""}
              onClick={() => setStatus(id)}
            >
              {label}{" "}
              <small>
                {query.data?.filter((o) => o.status === id).length || 0}
              </small>
            </button>
          ))}
        </div>
        <input
          aria-label="Поиск заказов"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Заказ, покупатель или лот"
        />
        <Button
          secondary
          onClick={() => void query.refetch()}
          disabled={query.isFetching}
        >
          <RefreshCw size={14} />
          Обновить
        </Button>
      </div>
      {query.isPending ? (
        <Pending />
      ) : query.isError ? (
        <Failure error={query.error} retry={() => void query.refetch()} />
      ) : !rows.length ? (
        <Empty
          title="Заказов с таким статусом пока нет"
          text="Здесь появятся заказы подключённых аккаунтов."
        />
      ) : (
        <div className="ws-table-scroll">
          <table className="ws-table">
            <thead>
              <tr>
                {["Заказ", "Покупатель", "Лот", "Сумма", "Статус", "Дата"].map(
                  (t) => (
                    <th key={t}>{t}</th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {rows.map((o) => (
                <tr key={o.id}>
                  <td>
                    <a
                      href={`https://funpay.com/orders/${encodeURIComponent(o.funpay_order_id)}/`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      #{o.funpay_order_id}
                    </a>
                  </td>
                  <td>{o.buyer_name}</td>
                  <td style={{ whiteSpace: "normal", maxWidth: 400 }}>
                    {o.description}
                  </td>
                  <td>{money(o.sum, o.currency)}</td>
                  <td>
                    <Status value={o.status} />
                  </td>
                  <td>
                    {o.date_source === "history_unknown"
                      ? "Дата не получена"
                      : date(o.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <footer className="ws-foot">
        Показано {rows.length} · Загружаются последние 1000 заказов
      </footer>
    </section>
  );
}
function DeliveryStatus() {
  const query = useData<{
    telegram: {
      enabled: boolean;
      configured: boolean;
      recipient: string;
      events: string[];
    };
  }>("notification-status", "/workspaces/notifications");
  return (
    <section className="ws-panel ws-pad ws-form">
      <div>
        <h3>Доставка уведомлений</h3>
        <p>
          Состояние подключённого канала. Уведомления отправляются по событиям,
          выбранным в настройках.
        </p>
      </div>
      {query.isPending ? (
        <Pending />
      ) : query.isError ? (
        <Failure error={query.error} retry={() => void query.refetch()} />
      ) : (
        <div className="ws-review">
          <strong>Telegram</strong>
          <span
            className={`ws-badge ${query.data?.telegram.enabled && query.data.telegram.configured ? "good" : ""}`}
          >
            {query.data?.telegram.enabled
              ? query.data.telegram.configured
                ? "Канал настроен"
                : "Укажите токен и получателя"
              : "Бот отключён в настройках"}
          </span>
          <small>
            Получатель: {query.data?.telegram.recipient || "не задан"}
          </small>
          <small>
            События: {query.data?.telegram.events.join(", ") || "не выбраны"}
          </small>
        </div>
      )}
      <div>
        <Link className="button secondary" to="/settings">
          Открыть настройки уведомлений
        </Link>
      </div>
    </section>
  );
}
export function InboxWorkspace() {
  const [tab, setTab] = useState("dialogs");
  return (
    <div className="workspace ws-form">
      <div className="page-head">
        <div>
          <span className="eyebrow">Коммуникации / 03</span>
          <h1>Входящие</h1>
          <p>Переписка с покупателями со всех аккаунтов в одном месте</p>
        </div>
        <Link className="button secondary" to="/settings">
          <Bell size={14} />
          Уведомления
        </Link>
      </div>
      <nav className="ws-tabs">
        {[
          ["dialogs", "Диалоги"],
          ["events", "События"],
          ["delivery", "Доставка"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "dialogs" && <InboxPage embedded />}
      {tab === "events" && <ActivityWorkspace chatOnly />}
      {tab === "delivery" && <DeliveryStatus />}
    </div>
  );
}
export function LotsWorkspace() {
  const [tab, setTab] = useState("lots");
  const account = useAccountStore((s) => s.selected);
  return (
    <div className="workspace ws-form">
      <nav className="ws-tabs">
        {[
          ["lots", "Лоты"],
          ["orders", "Заказы"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={id === tab ? "active" : ""}
            onClick={() => setTab(id)}
          >
            {id === "lots" ? <Package size={15} /> : <ShoppingCart size={15} />}
            {label}
          </button>
        ))}
      </nav>
      {tab === "lots" ? (
        <LotsPage />
      ) : (
        <OrdersWorkspace accountId={account ? Number(account) : undefined} />
      )}
    </div>
  );
}
