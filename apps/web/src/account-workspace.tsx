import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { useState } from "react";
import {
  Link,
  useNavigate,
  useParams,
  useSearchParams,
} from "react-router-dom";
import {
  ArrowLeft,
  ArrowUpRight,
  RefreshCw,
  ShieldCheck,
  MessageSquare,
  Star,
  Play,
  Square,
  ChevronRight,
} from "lucide-react";
import { request, date, money, useAction, useData } from "./api";
import {
  Avatar,
  Button,
  Empty,
  Failure,
  Field,
  Modal,
  Pending,
  Toggle,
} from "./ui";
import { AddAccount, InboxPage } from "./pages";
import { LotsPage } from "./lots-page";
import { OrdersWorkspace, ActivityWorkspace } from "./inbox-workspace";
import type { AccountPolicy, WorkspaceAccount } from "./workspace-types";
import "./workspaces.css";

export function AccountStatus({ account: a }: { account: WorkspaceAccount }) {
  const state = !a.is_active
    ? "Остановлен"
    : a.status === "online"
      ? "Работает"
      : a.status === "error" || a.status === "banned"
        ? "С ошибкой"
        : "Подключается";
  return (
    <span
      className={`ws-badge ${state === "Работает" ? "good" : state === "С ошибкой" ? "bad" : ""}`}
    >
      <i />
      {state}
    </span>
  );
}
function Identity({
  a,
  small = false,
}: {
  a: WorkspaceAccount;
  small?: boolean;
}) {
  return (
    <div className="ws-identity">
      <Avatar
        name={a.username || a.title}
        url={a.avatar_url}
        path={`/avatars/account/${a.id}`}
        small={small}
      />
      <div>
        <strong>{a.username || a.title}</strong>
        <small>
          #{a.funpay_id} <span> · RUB / USD / EUR</span>
        </small>
      </div>
    </div>
  );
}
function Today({ a }: { a: WorkspaceAccount }) {
  const rows = Object.entries(a.today);
  return (
    <>
      {rows.length
        ? rows.map(([c, v]) => <span key={c}>{money(v.sum, c)} </span>)
        : "Нет продаж"}
    </>
  );
}
function Stats({
  a,
  compact = false,
}: {
  a: WorkspaceAccount;
  compact?: boolean;
}) {
  return (
    <div className="ws-stats">
      {[
        ["Баланс", money(a.balance_rub)],
        ["Сегодня", <Today a={a} />],
        ["Заказов в работе", a.orders_in_work],
        ...(compact
          ? [
              [
                "Отзывы",
                `${a.review_count ?? "—"} · рейтинг ${a.rating ?? "—"}`,
              ],
            ]
          : [
              ["Рейтинг", a.rating ?? "—"],
              ["Отзывы", a.review_count ?? "—"],
            ]),
      ].map(([label, value], i) => (
        <div className="ws-stat" key={i}>
          <small>{label}</small>
          <strong>{value}</strong>
        </div>
      ))}
    </div>
  );
}
export function AccountsWorkspace() {
  const query = useData<WorkspaceAccount[]>(
      "workspace-accounts",
      "/workspaces/accounts",
    ),
    action = useAction(),
    navigate = useNavigate();
  const [search, setSearch] = useState(""),
    [status, setStatus] = useState("all"),
    [focused, setFocused] = useState<number | null>(null),
    [chosen, setChosen] = useState<number[]>([]);
  const rows = query.data || [];
  const state = (a: WorkspaceAccount) =>
    !a.is_active
      ? "stopped"
      : ["error", "banned"].includes(a.status)
        ? "error"
        : "running";
  const filtered = rows.filter(
    (a) =>
      (status === "all" || state(a) === status) &&
      `${a.username} ${a.title} ${a.funpay_id}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  const current = rows.find((a) => a.id === focused) || filtered[0];
  const counts = {
    all: rows.length,
    running: rows.filter((a) => state(a) === "running").length,
    error: rows.filter((a) => state(a) === "error").length,
    stopped: rows.filter((a) => state(a) === "stopped").length,
  };
  function check() {
    action.mutate({
      path: "/workspaces/accounts/check",
      body: { ids: chosen },
      success: "Проверка аккаунтов завершена",
    });
  }
  return (
    <div className="workspace ws-form">
      <header className="ws-head page-head">
        <div>
          <h1>Аккаунты</h1>
          <p>Ваши аккаунты FunPay и управление автоматизацией.</p>
        </div>
        <div className="ws-actions head-actions">
          <a
            className="button secondary"
            href="https://funpay.com/support/"
            target="_blank"
            rel="noreferrer"
          >
            <ShieldCheck size={15} />
            Поддержка FunPay
          </a>
          <Button
            secondary
            onClick={check}
            disabled={action.isPending || !rows.length}
          >
            <RefreshCw size={15} />
            Проверить {chosen.length ? `(${chosen.length})` : ""}
          </Button>
          <AddAccount />
        </div>
      </header>
      <div className="ws-panel ws-stats">
        {[
          ["Всего", counts.all],
          ["Работают", counts.running],
          ["С ошибкой", counts.error],
          ["Остановлены", counts.stopped],
          [
            "Баланс",
            money(rows.reduce((v, a) => v + Number(a.balance_rub), 0)),
          ],
        ].map(([k, v]) => (
          <div className="ws-stat" key={k}>
            <small>{k}</small>
            <strong>{v}</strong>
          </div>
        ))}
      </div>
      {query.isPending ? (
        <Pending />
      ) : query.isError ? (
        <Failure error={query.error} retry={() => void query.refetch()} />
      ) : !rows.length ? (
        <Empty
          title="Подключите первый аккаунт"
          text="Добавьте golden_key FunPay, чтобы загрузить профиль, лоты и переписки."
          action={<AddAccount />}
        />
      ) : (
        <div className="ws-layout">
          <section className="ws-panel">
            <div className="ws-toolbar">
              <input
                aria-label="Поиск аккаунтов"
                placeholder="Никнейм или ID FunPay"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
              <div className="segmented">
                {(
                  [
                    ["all", "Все"],
                    ["running", "Работают"],
                    ["error", "С ошибкой"],
                    ["stopped", "Остановлены"],
                  ] as const
                ).map(([id, label]) => (
                  <button
                    key={id}
                    className={status === id ? "active" : ""}
                    onClick={() => setStatus(id)}
                  >
                    {label} <small>{counts[id]}</small>
                  </button>
                ))}
              </div>
            </div>
            <div className="ws-table-scroll">
              <table className="ws-table">
                <thead>
                  <tr>
                    <th>
                      <input
                        type="checkbox"
                        aria-label="Выбрать показанные аккаунты"
                        checked={
                          filtered.length > 0 &&
                          filtered.every((a) => chosen.includes(a.id))
                        }
                        onChange={(e) =>
                          setChosen(
                            e.target.checked ? filtered.map((a) => a.id) : [],
                          )
                        }
                      />
                    </th>
                    {[
                      "Аккаунт",
                      "Статус",
                      "Баланс",
                      "Сегодня",
                      "Автоматизация",
                      "",
                    ].map((t) => (
                      <th key={t}>{t}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((a) => (
                    <tr
                      key={a.id}
                      className={current?.id === a.id ? "selected" : ""}
                      onClick={() => setFocused(a.id)}
                      onDoubleClick={() => navigate(`/accounts/${a.id}`)}
                    >
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`Выбрать ${a.username}`}
                          checked={chosen.includes(a.id)}
                          onClick={(e) => e.stopPropagation()}
                          onChange={(e) =>
                            setChosen(
                              e.target.checked
                                ? [...chosen, a.id]
                                : chosen.filter((id) => id !== a.id),
                            )
                          }
                        />
                      </td>
                      <td>
                        <Identity a={a} small />
                      </td>
                      <td>
                        <AccountStatus account={a} />
                      </td>
                      <td>{money(a.balance_rub)}</td>
                      <td>
                        <Today a={a} />
                      </td>
                      <td>
                        <span
                          title="Автоподнятие"
                          className={`ws-badge ${a.automation.auto_raise ? "good" : ""}`}
                        >
                          <ArrowUpRight size={14} />
                        </span>{" "}
                        <span
                          title="Автоответчик"
                          className={`ws-badge ${a.responder_enabled ? "good" : ""}`}
                        >
                          <MessageSquare size={14} />
                        </span>{" "}
                        <span
                          title="Отзывы"
                          className={`ws-badge ${a.automation.review_reply && a.review_master ? "good" : ""}`}
                        >
                          <Star size={14} />
                        </span>
                      </td>
                      <td>
                        <Link
                          aria-label={`Открыть ${a.username}`}
                          to={`/accounts/${a.id}`}
                        >
                          <ChevronRight size={17} />
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!filtered.length && (
              <div className="ws-empty">Аккаунты не найдены</div>
            )}
            <footer className="ws-foot">
              <span>
                Аккаунтов: {filtered.length} · Выбрано: {chosen.length}
              </span>
              <span>Данные обновляются автоматически</span>
            </footer>
          </section>
          {current && (
            <aside className="ws-panel ws-pad ws-side">
              <div className="ws-row">
                <Identity a={current} />
                <AccountStatus account={current} />
              </div>
              <Stats a={current} compact />
              <span className="ws-badge">
                <ShieldCheck size={14} />
                {current.proxy_label}
              </span>
              {current.last_error && (
                <p className="text-red">{current.last_error}</p>
              )}
              <h3>Автоматизация</h3>
              <div className="ws-rows">
                {[
                  ["Автоподнятие", current.automation.auto_raise],
                  ["Автоответы", current.responder_enabled],
                  [
                    "Ответы на отзывы",
                    current.automation.review_reply && current.review_master,
                  ],
                ].map(([name, on]) => (
                  <div className="ws-row" key={String(name)}>
                    <span>{name}</span>
                    <small className={on ? "text-green" : ""}>
                      {on ? "Вкл." : "Выкл."}
                    </small>
                  </div>
                ))}
              </div>
              <div className="ws-side-links">
                {[
                  ["lots", "Лоты"],
                  ["chats", "Чаты"],
                  ["orders", "Заказы"],
                  ["logs", "Логи"],
                  ["settings", "Настройки"],
                ].map(([id, name]) => (
                  <Link key={id} to={`/accounts/${current.id}?tab=${id}`}>
                    {name}
                  </Link>
                ))}
              </div>
              <div className="ws-actions head-actions">
                <Link className="button primary" to={`/accounts/${current.id}`}>
                  Открыть аккаунт
                </Link>
                <Button
                  secondary
                  disabled={action.isPending}
                  onClick={() =>
                    action.mutate({
                      path: `/accounts/${current.id}`,
                      method: "PATCH",
                      body: { is_active: !current.is_active },
                    })
                  }
                >
                  {current.is_active ? (
                    <Square size={14} />
                  ) : (
                    <Play size={14} />
                  )}
                  {current.is_active ? "Остановить" : "Запустить"}
                </Button>
              </div>
            </aside>
          )}
        </div>
      )}
    </div>
  );
}

export function PolicyEditor({
  account,
  only,
}: {
  account: WorkspaceAccount;
  only?: "confirmation";
}) {
  const [draft, setDraft] = useState<AccountPolicy | null>(null);
  const [dirty, setDirty] = useState(false);
  const cfg = draft || account.automation;
  function set<K extends keyof AccountPolicy>(key: K, value: AccountPolicy[K]) {
    setDraft({ ...cfg, [key]: value }); setDirty(true);
  }
  const auto = useAutoSave({ value: cfg, dirty, save: value => request(`/workspaces/accounts/${account.id}/automation`, "PUT", value), onSaved: () => setDirty(false), queryKeys: ["account-workspace", "responder-workspace"] });
  const text = (
    key:
      | "confirmation_text"
      | "reminder_text"
      | "review_chat_text"
      | "review_reminder_text",
    label: string,
  ) => (
    <Field label={label}>
      <textarea
        aria-label={label}
        maxLength={5000}
        value={cfg[key]}
        onChange={(e) => set(key, e.target.value)}
      />
      <small>
        Переменные: {"{buyer_name}, {order_id}, {order_link}, {seller_name}"}
      </small>
    </Field>
  );
  const toggle = (
    key:
      | "auto_raise"
      | "confirmation"
      | "confirmation_reminder"
      | "review_reply"
      | "review_chat"
      | "review_reminder",
    title: string,
    hint: string,
  ) => (
    <div className="ws-row">
      <div>
        <strong>{title}</strong>
        <small>{hint}</small>
      </div>
      <Toggle checked={cfg[key]} label={title} onChange={(v) => set(key, v)} />
    </div>
  );
  return (
    <div className="ws-form">
      {!only && (
        <section className="ws-panel ws-pad ws-form">
          {toggle(
            "auto_raise",
            "Автоподнятие лотов",
            "Поднимать все категории аккаунта с учётом ограничений FunPay",
          )}
          {cfg.auto_raise && (
            <Field label="Интервал поднятия, минут">
              <input
                type="number"
                min={1}
                max={1440}
                value={cfg.raise_interval / 60}
                onChange={(e) =>
                  set("raise_interval", Math.round(Number(e.target.value) * 60))
                }
              />
            </Field>
          )}
        </section>
      )}
      <section className="ws-panel ws-pad ws-form">
        {toggle(
          "confirmation",
          "Ответ после подтверждения заказа",
          "Покупатель подтвердил выполнение заказа",
        )}
        {cfg.confirmation &&
          text("confirmation_text", "Сообщение после подтверждения")}
        {toggle(
          "confirmation_reminder",
          "Напоминание подтвердить заказ",
          "Перед отправкой проверим статус заказа. Аренды и очереди подарков пропускаются.",
        )}
        {cfg.confirmation_reminder && (
          <>
            <div className="ws-grid">
              <Field label="Первое напоминание через, минут">
                <input
                  type="number"
                  min={5}
                  max={10080}
                  value={cfg.reminder_minutes}
                  onChange={(e) =>
                    set("reminder_minutes", Number(e.target.value))
                  }
                />
              </Field>
              <Field label="Количество напоминаний">
                <select
                  value={cfg.reminder_count}
                  onChange={(e) =>
                    set("reminder_count", Number(e.target.value))
                  }
                >
                  {[1, 2, 3].map((n) => (
                    <option key={n} value={n}>
                      {n}
                    </option>
                  ))}
                </select>
              </Field>
            </div>
            {text("reminder_text", "Текст напоминания")}
          </>
        )}
      </section>
      {!only && (
        <section className="ws-panel ws-pad ws-form">
          {toggle(
            "review_reply",
            "Ответы на отзывы",
            "Шаблоны для каждой оценки находятся в автоответчике",
          )}
          {cfg.review_reply && (
            <Link className="ws-link" to="/auto-replies">
              Настроить пять оценок и тексты ответов →
            </Link>
          )}
          {toggle(
            "review_chat",
            "Сообщение после отзыва",
            "Поблагодарить покупателя в чате",
          )}
          {cfg.review_chat &&
            text("review_chat_text", "Сообщение после отзыва")}
          {toggle(
            "review_reminder",
            "Напоминание оставить отзыв",
            "Один раз для завершённого заказа без отзыва",
          )}
          {cfg.review_reminder &&
            text("review_reminder_text", "Просьба оставить отзыв")}
        </section>
      )}
      <AutoSaveStatus state={auto} />
    </div>
  );
}
function KeyEditor({ account }: { account: WorkspaceAccount }) {
  const [key, setKey] = useState(""),
    action = useAction();
  return (
    <section className="ws-panel ws-pad ws-form">
      <div>
        <h3>Подключение FunPay</h3>
        <p>
          Замените ключ этого аккаунта. Лоты, переписки и настройки сохранятся.
        </p>
      </div>
      <Field label="Новый golden_key">
        <input
          autoComplete="new-password"
          type="password"
          aria-label="Новый golden_key"
          value={key}
          onChange={(e) => setKey(e.target.value)}
          placeholder="Вставьте новый ключ"
        />
      </Field>
      <div>
        <Button
          disabled={!key.trim() || action.isPending}
          onClick={() =>
            void action
              .mutateAsync({
                path: `/workspaces/accounts/${account.id}/key`,
                method: "PUT",
                body: { golden_key: key },
                success: "Ключ проверен и обновлён",
              })
              .then(() => setKey(""))
              .catch(() => {})
          }
        >
          Обновить ключ
        </Button>
      </div>
      <small>
        Ключ хранится в зашифрованном виде и не показывается после сохранения.
      </small>
    </section>
  );
}
function AccountConnection({ account }: { account: WorkspaceAccount }) {
  const proxies = useData<{ id: number; host: string; port: number }[]>(
      "proxies",
      "/proxies",
    ),
    action = useAction(),
    navigate = useNavigate();
  const [proxy, setProxy] = useState(String(account.proxy_id || "")),
    [deleting, setDeleting] = useState(false),
    [name, setName] = useState("");
  const auto = useAutoSave({ value: { proxy_id: proxy ? Number(proxy) : null }, dirty: proxy !== String(account.proxy_id || ""),
    save: value => request(`/accounts/${account.id}`, "PATCH", value), queryKeys: ["account-workspace", "accounts"] });
  return (
    <section className="ws-panel ws-pad ws-form">
      <h3>Соединение и управление</h3>
      <Field label="Прокси аккаунта">
        <select value={proxy} onChange={(e) => setProxy(e.target.value)}>
          <option value="">Напрямую, без прокси</option>
          {proxies.data?.map((p) => (
            <option key={p.id} value={p.id}>
              {p.host}:{p.port}
            </option>
          ))}
        </select>
      </Field>
      <div className="ws-actions head-actions">
        <AutoSaveStatus state={auto} />
        <Link to="/proxies" className="ws-link">
          Управлять прокси
        </Link>
        <Button secondary className="danger" onClick={() => setDeleting(true)}>
          Удалить аккаунт из приложения
        </Button>
      </div>
      <Modal
        open={deleting}
        onOpenChange={setDeleting}
        title="Удалить аккаунт"
        description="Будут удалены локальные переписки, лоты и настройки этого аккаунта. Steam-аккаунты и история аренды сохранятся без привязки к удалённому аккаунту. Профиль FunPay останется на сайте."
      >
        <div className="ws-form">
          <Field label={`Введите имя ${account.username} для подтверждения`}>
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
          <Button
            className="danger"
            disabled={action.isPending || name !== account.username}
            onClick={() =>
              void action
                .mutateAsync({
                  path: `/accounts/${account.id}`,
                  method: "DELETE",
                  success: "Аккаунт удалён из приложения",
                })
                .then(() => navigate("/accounts"))
                .catch(() => {})
            }
          >
            Удалить локальные данные
          </Button>
        </div>
      </Modal>
    </section>
  );
}
export function AccountWorkspace() {
  const { accountId } = useParams(),
    [params, setParams] = useSearchParams(),
    action = useAction();
  const query = useData<WorkspaceAccount>(
    "workspace-account",
    `/workspaces/accounts/${accountId}`,
  );
  const a = query.data,
    tab = params.get("tab") || "overview";
  const [docs, setDocs] = useState(false);
  if (query.isPending) return <Pending />;
  if (query.isError || !a)
    return <Failure error={query.error} retry={() => void query.refetch()} />;
  return (
    <div className="workspace ws-form">
      <Link to="/accounts" className="ws-back">
        <ArrowLeft size={14} />
        Аккаунты / {a.username}
      </Link>
      <section className="ws-panel ws-pad ws-hero">
        <div className="ws-head page-head">
          <div className="ws-actions head-actions">
            <Identity a={a} />
            <AccountStatus account={a} />
          </div>
          <div className="ws-actions head-actions">
            <Button
              secondary
              disabled={action.isPending}
              onClick={() =>
                action.mutate({
                  path: `/accounts/${a.id}/sync`,
                  success: "Профиль обновлён",
                })
              }
            >
              <RefreshCw size={14} />
              Обновить
            </Button>
            <Button
              secondary
              disabled={action.isPending}
              onClick={() =>
                action.mutate({
                  path: `/accounts/${a.id}`,
                  method: "PATCH",
                  body: { is_active: !a.is_active },
                })
              }
            >
              {a.is_active ? <Square size={14} /> : <Play size={14} />}
              {a.is_active ? "Остановить" : "Запустить"}
            </Button>
            <a
              className="button secondary"
              href={`https://funpay.com/users/${a.funpay_id}/`}
              target="_blank"
              rel="noreferrer"
            >
              FunPay <ArrowUpRight size={14} />
            </a>
          </div>
        </div>
        <Stats a={a} />
      </section>
      <nav className="ws-tabs" aria-label="Разделы аккаунта">
        {[
          ["overview", "Обзор"],
          ["lots", "Лоты"],
          ["chats", "Чаты"],
          ["orders", "Заказы"],
          ["logs", "Логи"],
          ["settings", "Настройки"],
        ].map(([id, label]) => (
          <button
            key={id}
            className={tab === id ? "active" : ""}
            onClick={() => setParams({ tab: id })}
          >
            {label}
          </button>
        ))}
      </nav>
      {tab === "overview" && (
        <div className="ws-grid">
          <section className="ws-panel ws-pad">
            <h3>Автоматизация</h3>
            <p>Что приложение делает на этом аккаунте</p>
            {[
              ["auto_raise", "Автоподнятие лотов", "Подъём по расписанию"],
              [
                "confirmation",
                "Ответ при подтверждении",
                "Сообщение после завершения заказа",
              ],
              ["review_reply", "Ответ на отзыв", "Шаблоны для каждой оценки"],
            ].map(([key, title, hint]) => (
              <div className="ws-row" key={key}>
                <div>
                  <strong>{title}</strong>
                  <small>{hint}</small>
                </div>
                <div className="ws-actions head-actions">
                  <button
                    className="ws-back"
                    onClick={() => setParams({ tab: "settings" })}
                  >
                    Настроить
                  </button>
                  <Toggle
                    label={title}
                    checked={Boolean(a.automation[key as keyof AccountPolicy])}
                    disabled={action.isPending}
                    onChange={(v) =>
                      action.mutate({
                        path: `/workspaces/accounts/${a.id}/automation`,
                        method: "PUT",
                        body: { ...a.automation, [key]: v },
                      })
                    }
                  />
                </div>
              </div>
            ))}
          </section>
          <section className="ws-panel ws-pad">
            <h3>Подключение</h3>
            <p>
              Обновлено{" "}
              {a.last_synced_at ? date(a.last_synced_at) : "ещё не проверено"}
            </p>
            <div className="ws-row">
              <span>Статус</span>
              <AccountStatus account={a} />
            </div>
            <div className="ws-row">
              <span>Golden key</span>
              <span>••••••••••••</span>
              <Button secondary onClick={() => setParams({ tab: "settings" })}>
                Обновить ключ
              </Button>
            </div>
            <div className="ws-row">
              <span>Соединение</span>
              <small>{a.proxy_label}</small>
            </div>
            {a.last_error && <p className="text-red">{a.last_error}</p>}
            <Button secondary onClick={() => setDocs(true)}>
              Как работает автоматизация
            </Button>
          </section>
        </div>
      )}
      {tab === "lots" && <LotsPage accountId={a.id} />}{" "}
      {tab === "chats" && <InboxPage key={a.id} accountId={a.id} embedded />}
      {tab === "orders" && <OrdersWorkspace accountId={a.id} />}{" "}
      {tab === "logs" && <ActivityWorkspace accountId={a.id} />}{" "}
      {tab === "settings" && (
        <>
          <KeyEditor account={a} />
          <AccountConnection key={`connection-${a.id}`} account={a} />
          <PolicyEditor key={a.id} account={a} />
        </>
      )}
      <Modal
        open={docs}
        onOpenChange={setDocs}
        title="Автоматизация аккаунта"
        description="Настройки применяются только к выбранному аккаунту."
      >
        <p>
          Остановка аккаунта приостанавливает его фоновые задачи. Автоподнятие
          соблюдает ограничения FunPay. Ответы и напоминания сохраняют историю
          отправки и проверяют статус заказа перед отправкой.
        </p>
        <p>
          Общие приветствия, команды и шаблоны отзывов настраиваются в плагине
          «Автоответчик».
        </p>
      </Modal>
    </div>
  );
}
