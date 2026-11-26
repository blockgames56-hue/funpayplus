import { MotionBanner } from "./lottie-mark";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { requestId } from "./request-id";
import { AppearanceSettings } from "./appearance";
import { PluginPart } from "./plugin-sections";
import { useInfiniteQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { useVirtualizer } from "@tanstack/react-virtual";
import {
  Activity as ActivityIcon,
  ArrowDownLeft,
  ArrowUpRight,
  CheckCheck,
  ChevronDown,
  Clock3,
  Coins,
  ImagePlus,
  KeyRound,
  LoaderCircle,
  MoreHorizontal,
  Pin,
  Plus,
  RefreshCw,
  Search,
  Send,
  ShieldCheck,
  Trash2,
  Wallet,
  X,
  Zap,
} from "lucide-react";
import { toast } from "sonner";
import {
  accountQuery,
  date,
  money,
  request,
  sound,
  useAction,
  useData,
} from "./api";
import { useAccountStore, useChatStore, useSettingsStore } from "./store";
import {
  Avatar,
  Button,
  Confirm,
  Empty,
  External,
  Failure,
  Field,
  Form,
  Modal,
  Pending,
  Status,
  Toggle,
  ZoomImage,
} from "./ui";
import type {
  Account,
  Activity,
  AppSettings,
  Blacklist,
  Chat,
  Dashboard,
  Health,
  Message,
  Order,
  Plugins,
  ProxyServer,
  Rule,
  Telegram,
} from "./types";

export { navigation } from "./navigation";

function AccountSelect({
  name = "account_id",
  accounts,
  value,
  onChange,
}: {
  name?: string;
  accounts: Account[];
  value?: string;
  onChange?: (value: string) => void;
}) {
  return (
    <select
      name={name}
      required
      value={value}
      onChange={onChange ? (event) => onChange(event.target.value) : undefined}
    >
      <option value="">Выберите аккаунт</option>
      {accounts.map((account) => (
        <option key={account.id} value={account.id}>
          {account.username}
        </option>
      ))}
    </select>
  );
}
function PageHead({
  eyebrow,
  title,
  text,
  children,
}: {
  eyebrow: string;
  title: string;
  text: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="page-head">
      <div>
        <span className="eyebrow">{eyebrow}</span>
        <h1>{title}</h1>
        <p>{text}</p>
      </div>
      <div className="head-actions">{children}</div>
    </div>
  );
}
function DataGuard({
  query,
  children,
}: {
  query: {
    isPending: boolean;
    isError: boolean;
    error: Error | null;
    refetch: () => unknown;
  };
  children: React.ReactNode;
}) {
  if (query.isPending) return <Pending />;
  if (query.isError)
    return (
      <Failure
        error={query.error}
        retry={() => {
          void query.refetch();
        }}
      />
    );
  return <>{children}</>;
}
export function AddAccount() {
  const [open, setOpen] = useState(false);
  const action = useAction();
  const proxies = useData<ProxyServer[]>("proxies", "/proxies");
  return (
    <Modal
      open={open}
      onOpenChange={setOpen}
      trigger={
        <Button>
          <Plus size={16} />
          Подключить аккаунт
        </Button>
      }
      title="Подключение FunPay"
      description="Проверим сессию перед сохранением. Golden key хранится в зашифрованном виде."
    >
      <Form
        onSubmit={async (form) => {
          await action.mutateAsync({
            path: "/accounts",
            body: {
              golden_key: form.get("golden_key"),
              proxy_id: Number(form.get("proxy_id")) || null,
              ...(form.get("user_agent")
                ? { user_agent: form.get("user_agent") }
                : {}),
            },
            success: "Аккаунт подключён",
          });
          setOpen(false);
        }}
      >
        <Field
          label="Golden key"
          hint="Значение cookie golden_key из вашей авторизованной сессии funpay.com."
        >
          <input
            name="golden_key"
            type="password"
            autoComplete="off"
            minLength={16}
            required
            placeholder="••••••••••••••••••••••••"
          />
        </Field>
        <Field label="Прокси">
          <select name="proxy_id">
            <option value="">Прямое соединение</option>
            {proxies.data?.map((proxy) => (
              <option key={proxy.id} value={proxy.id}>
                {proxy.host}:{proxy.port}
              </option>
            ))}
          </select>
          <Link to="/proxies" onClick={() => setOpen(false)}>
            Добавить прокси
          </Link>
        </Field>
        <Field
          label="User-Agent"
          hint="Оставьте пустым для стандартного Chrome User-Agent."
        >
          <input name="user_agent" placeholder="Mozilla/5.0…" maxLength={500} />
        </Field>
      </Form>
    </Modal>
  );
}

export function Overview() {
  const selected = useAccountStore((state) => state.selected);
  const [period, setPeriod] = useState("days");
  const [currency, setCurrency] = useState<"RUB" | "USD" | "EUR">("RUB");
  const query = useData<Dashboard>(
    "dashboard",
    accountQuery(`/dashboard?period=${period}`, selected),
  );
  const data = query.data;
  return (
    <>
      <PageHead
        eyebrow="Торговый терминал / 01"
        title="Всё под контролем"
        text="Аккаунты, продажи и автоматизация. В одном рабочем пространстве."
      >
        <span className="live-label">
          <i />
          Данные вашего магазина
        </span>
      </PageHead>
      <MotionBanner title="Ваш магазин — в движении." text="Следите за продажами, общайтесь с покупателями и управляйте автоматизацией в одном пространстве." label="FUNPAY PLUS · РАБОЧИЙ РИТМ" />
      <DataGuard query={query}>
        {data && (
          <>
            <div className="metrics">
              <Metric
                title="Общий баланс"
                value={money(data.balances[currency], currency)}
                icon={<Wallet size={18} />}
                detail={
                  <div className="currency-row">
                    {(["RUB", "USD", "EUR"] as const).map((item) => (
                      <button
                        className={currency === item ? "active" : ""}
                        key={item}
                        onClick={() => setCurrency(item)}
                      >
                        {item}
                      </button>
                    ))}
                  </div>
                }
              />
              <Metric
                title="Продажи сегодня"
                value={money(data.today[currency], currency)}
                icon={<ArrowDownLeft size={18} />}
                detail={`${data.today_count} заказов · локальный день`}
              />
              <Metric
                title="После комиссии / месяц"
                value={money(data.monthly_net[currency], currency)}
                icon={<Coins size={18} />}
                detail={`Комиссия ${data.commission_percent}% · без себестоимости`}
              />
              <Metric
                title="Здоровье сессий"
                value={`${data.online} / ${data.total}`}
                icon={<ShieldCheck size={18} />}
                detail={
                  <span className={data.errors ? "text-red" : "text-green"}>
                    {data.errors
                      ? `${data.errors} требуют внимания`
                      : "Нет ошибок сессий"}
                  </span>
                }
              />
            </div>
            <div className="overview-grid">
              <section className="panel chart-panel">
                <div className="panel-head">
                  <div>
                    <span className="eyebrow">Динамика магазина</span>
                    <h2>
                      Выручка <span className="muted">/ {currency}</span>
                    </h2>
                  </div>
                  <div className="segmented">
                    {[
                      ["hours", "24 часа"],
                      ["days", "30 дней"],
                      ["months", "12 месяцев"],
                    ].map(([value, label]) => (
                      <button
                        key={value}
                        className={period === value ? "active" : ""}
                        onClick={() => setPeriod(value)}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="chart">
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart
                      data={data.series}
                      margin={{ left: 0, right: 18, top: 20, bottom: 0 }}
                    >
                      <defs>
                        <linearGradient
                          id="revenue-fill"
                          x1="0"
                          y1="0"
                          x2="0"
                          y2="1"
                        >
                          <stop
                            offset="0%"
                            stopColor="var(--green)"
                            stopOpacity={0.25}
                          />
                          <stop
                            offset="100%"
                            stopColor="var(--green)"
                            stopOpacity={0}
                          />
                        </linearGradient>
                      </defs>
                      <CartesianGrid stroke="var(--line)" vertical={false} />
                      <XAxis
                        dataKey="label"
                        axisLine={false}
                        tickLine={false}
                        minTickGap={32}
                        tick={{ fill: "var(--muted)", fontSize: 12 }}
                      />
                      <YAxis
                        axisLine={false}
                        tickLine={false}
                        width={55}
                        tick={{ fill: "var(--muted)", fontSize: 12 }}
                      />
                      <Tooltip
                        contentStyle={{
                          background: "var(--panel)",
                          color: "rgb(var(--ui-text-rgb))",
                          border: "1px solid var(--line)",
                          borderRadius: 8,
                        }}
                        formatter={(value) => money(Number(value), currency)}
                      />
                      <Area
                        type="monotone"
                        dataKey={currency}
                        name="Выручка"
                        stroke="var(--green)"
                        strokeWidth={2}
                        fill="url(#revenue-fill)"
                      />
                    </AreaChart>
                  </ResponsiveContainer>
                </div>
                {!data.today_count && (
                  <div className="chart-note">
                    <span className="dot" /> График заполнится после
                    синхронизации ваших заказов
                  </div>
                )}
              </section>
              <section className="panel system-card">
                <div className="orb">
                  <Zap size={34} />
                </div>
                <span className="eyebrow">FunPay Plus</span>
                <h2>
                  Ваш магазин.
                  <br />
                  Ваш ритм.
                </h2>
                <p>
                  Подключите аккаунт, синхронизируйте лоты и настройте
                  автоматизацию.
                </p>
                <Link className="button secondary" to="/accounts">
                  Управлять аккаунтами
                  <ArrowUpRight size={16} />
                </Link>
                <div className="system-bottom">
                  <i />
                  Данные защищены · AES-GCM
                </div>
              </section>
            </div>
            <section className="panel">
              <div className="panel-head">
                <div>
                  <span className="eyebrow">Журнал событий</span>
                  <h2>Последняя активность</h2>
                </div>
                <Link className="text-link" to="/health">
                  Весь журнал
                  <ArrowUpRight size={14} />
                </Link>
              </div>
              <ActivityList rows={data.activities} />
            </section>
          </>
        )}
      </DataGuard>
    </>
  );
}
function Metric({
  title,
  value,
  icon,
  detail,
}: {
  title: string;
  value: string;
  icon: React.ReactNode;
  detail: React.ReactNode;
}) {
  return (
    <section className="metric">
      <div className="metric-title">
        {title}
        <span>{icon}</span>
      </div>
      <strong>{value}</strong>
      <div className="metric-detail">{detail}</div>
    </section>
  );
}
function ActivityList({ rows }: { rows: Activity[] }) {
  return rows.length ? (
    <div className="activity-list">
      {rows.map((row) => (
        <div key={row.id} className="activity-row">
          <span className={`event-icon ${row.level === "error" ? "red" : ""}`}>
            <ActivityIcon size={14} />
          </span>
          <div>
            <p>{row.text}</p>
            <small>
              {row.channel}
              {row.account_id ? ` / аккаунт ${row.account_id}` : ""}
            </small>
          </div>
          <time>{date(row.created_at)}</time>
        </div>
      ))}
    </div>
  ) : (
    <Empty
      title="Здесь появится история магазина"
      text="Подключения, сообщения и поднятия лотов сохраняются автоматически."
    />
  );
}

export function AccountsPage() {
  const query = useData<Account[]>("accounts", "/accounts");
  const proxies = useData<ProxyServer[]>("proxies", "/proxies");
  const action = useAction();
  const selected = useAccountStore((s) => s.selected);
  const rows =
    query.data?.filter((row) => !selected || String(row.id) === selected) || [];
  return (
    <>
      <PageHead
        eyebrow="Подключения / 02"
        title="Аккаунты"
        text="Отдельная сессия и прокси для каждого магазина."
      >
        <AddAccount />
      </PageHead>
      <DataGuard query={query}>
        {rows.length ? (
          <div className="account-grid">
            {rows.map((account) => {
              const proxy = proxies.data?.find(
                (item) => item.id === account.proxy_id,
              );
              return (
                <article className="panel account-card" key={account.id}>
                  <div className="account-top">
                    <Avatar
                      name={account.username}
                      url={account.avatar_url}
                      path={`/avatars/account/${account.id}`}
                    />
                    <div>
                      <h2>{account.username}</h2>
                      <External
                        href={`https://funpay.com/users/${account.funpay_id}/`}
                      >
                        {account.username} · #{account.funpay_id}
                      </External>
                    </div>
                    <Status value={account.status} />
                  </div>
                  <div className="account-balance">
                    <small>Доступный баланс</small>
                    <strong>{money(account.balance_rub)}</strong>
                    <span>
                      {money(account.balance_usd, "USD")} <i> / </i>
                      {money(account.balance_eur, "EUR")}
                    </span>
                  </div>
                  <div className="account-meta">
                    <span>Соединение</span>
                    <b>
                      {proxy ? `${proxy.host}:${proxy.port}` : "Прямое"}{" "}
                      {proxy?.ping_ms != null && (
                        <em
                          className={
                            proxy.ping_ms < 100
                              ? "text-green"
                              : proxy.ping_ms < 300
                                ? "text-amber"
                                : "text-red"
                          }
                        >
                          {proxy.ping_ms} ms
                        </em>
                      )}
                    </b>
                    <span>Обновлено</span>
                    <b>{date(account.last_synced_at)}</b>
                  </div>
                  {account.last_error && (
                    <p className="inline-error">{account.last_error}</p>
                  )}
                  <div className="account-actions">
                    <Button
                      secondary
                      disabled={action.isPending}
                      onClick={() =>
                        action.mutate({
                          path: `/accounts/${account.id}/sync`,
                          success: "Профиль обновлён",
                        })
                      }
                    >
                      <RefreshCw size={14} />
                      Обновить
                    </Button>
                    <Modal
                      trigger={
                        <button className="icon-button" title="Прокси аккаунта">
                          <MoreHorizontal size={18} />
                        </button>
                      }
                      title="Прокси аккаунта"
                      description="После сохранения фоновая сессия будет перезапущена."
                    >
                      <Form
                        onSubmit={(form) =>
                          action.mutateAsync({
                            path: `/accounts/${account.id}`,
                            method: "PATCH",
                            body: {
                              proxy_id: Number(form.get("proxy_id")) || null,
                            },
                            success: "Прокси обновлён",
                          })
                        }
                      >
                        <Field label="Прокси">
                          <select
                            name="proxy_id"
                            defaultValue={account.proxy_id || ""}
                          >
                            <option value="">Прямое соединение</option>
                            {proxies.data?.map((p) => (
                              <option key={p.id} value={p.id}>
                                {p.host}:{p.port}
                              </option>
                            ))}
                          </select>
                        </Field>
                      </Form>
                    </Modal>
                    <Toggle
                      label="Фоновая синхронизация"
                      checked={account.is_active}
                      onChange={(value) =>
                        action.mutate({
                          path: `/accounts/${account.id}`,
                          method: "PATCH",
                          body: { is_active: value },
                        })
                      }
                    />
                    <Confirm
                      trigger={
                        <button
                          className="icon-button danger"
                          title="Удалить аккаунт"
                        >
                          <Trash2 size={16} />
                        </button>
                      }
                      title="Удалить аккаунт?"
                      text="Локальная история диалогов, лотов и правил этого аккаунта будет удалена. Steam-аккаунты и история аренды сохранятся без привязки к нему."
                      onConfirm={() =>
                        action.mutateAsync({
                          path: `/accounts/${account.id}`,
                          method: "DELETE",
                          success: "Аккаунт удалён",
                        })
                      }
                    />
                  </div>
                </article>
              );
            })}
          </div>
        ) : (
          <section className="panel">
            <Empty
              title="Начните с первого аккаунта"
              text="Подключите свою сессию FunPay, чтобы видеть баланс, лоты и диалоги."
              action={<AddAccount />}
            />
          </section>
        )}
      </DataGuard>
    </>
  );
}

function conversationDate(value: string) {
  const moment = new Date(
    /[Zz]|[+-]\d{2}:\d{2}$/.test(value) ? value : `${value}Z`,
  );
  if (!Number.isFinite(moment.getTime()) || moment.getFullYear() < 1971)
    return "Дата неизвестна";
  const today = new Date();
  const yesterday = new Date();
  yesterday.setDate(yesterday.getDate() - 1);
  const time = moment.toLocaleTimeString("ru-RU", {
    hour: "2-digit",
    minute: "2-digit",
  });
  if (moment.toDateString() === today.toDateString()) return time;
  if (moment.toDateString() === yesterday.toDateString())
    return `Вчера, ${time}`;
  return moment.toLocaleString("ru-RU", {
    day: "2-digit",
    month: "short",
    year: moment.getFullYear() === today.getFullYear() ? undefined : "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function InboxPage({
  accountId,
  embedded = false,
}: { accountId?: number; embedded?: boolean } = {}) {
  const [dateSort, setDateSort] = useState("newest");
  const [period, setPeriod] = useState("all");
  const after = useMemo(() => {
    if (period === "all") return "";
    const start = new Date();
    start.setHours(0, 0, 0, 0);
    start.setDate(
      start.getDate() - (period === "week" ? 6 : period === "month" ? 29 : 0),
    );
    return `&after=${encodeURIComponent(start.toISOString())}`;
  }, [period]);
  const selectAccount = useAccountStore((state) => state.select);
  const [selectedAccount, setSelectedAccount] = useState(
    accountId ? String(accountId) : "all",
  );
  const [loadingChatId, setLoadingChatId] = useState<number | null>(null);
  const selected = useChatStore((state) => state.selected);
  const select = useChatStore((state) => state.select);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [text, setText] = useState("");
  const sendBusy = useRef(false);
  const pendingSend = useRef<{ chat: number; text: string; key: string } | null>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState("");
  const action = useAction();
  const input = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const messageRef = useRef<HTMLDivElement>(null);
  const chats = useData<Chat[]>(
    "chats",
    accountQuery(
      `/chats?q=${encodeURIComponent(search)}&unread=${filter === "unread"}&active_order=${filter === "order"}&sort=${dateSort}${after}`,
      selectedAccount === "all" ? "" : selectedAccount,
    ),
    Boolean(selectedAccount),
  );
  const messages = useInfiniteQuery({
    queryKey: ["messages", selected],
    queryFn: ({ pageParam }) =>
      request<Message[]>(
        `/chats/${selected}/messages${pageParam ? `?before=${pageParam}` : ""}`,
      ),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (page) => (page.length === 100 ? page[0].id : undefined),
    enabled: selected !== null && !!chats.data?.some((c) => c.id === selected),
    refetchInterval: 30000,
  });
  const messagesData = useMemo(
    () => messages.data?.pages.flat().sort((a, b) => a.id - b.id) || [],
    [messages.data],
  );
  const newestMessage = useRef<number | null>(null);
  const accounts = useData<Account[]>("accounts", "/accounts");
  const orders = useData<Order[]>(
    "orders",
    accountQuery("/orders", selectedAccount === "all" ? "" : selectedAccount),
    Boolean(selectedAccount),
  );
  const chat = chats.data?.find((item) => item.id === selected);
  const order = orders.data?.find(
    (item) => item.funpay_order_id === chat?.current_order_id,
  );
  const [notes, setNotes] = useState("");
  const virtual = useVirtualizer({
    count: chats.data?.length || 0,
    getScrollElement: () => listRef.current,
    estimateSize: () => 88,
    overscan: 8,
  });
  const messageVirtual = useVirtualizer({
    count: messagesData.length,
    getScrollElement: () => messageRef.current,
    estimateSize: () => 100,
    overscan: 12,
  });
  useEffect(() => {
    setNotes(chat?.notes || "");
  }, [chat?.id, chat?.notes]);
  useEffect(() => {
    if (!file) {
      setPreview("");
      return;
    }
    const url = URL.createObjectURL(file);
    setPreview(url);
    return () => URL.revokeObjectURL(url);
  }, [file]);
  useEffect(() => {
    const latest = messagesData.at(-1)?.id || null;
    if (latest && latest !== newestMessage.current) {
      messageVirtual.scrollToIndex(messagesData.length - 1, { align: "end" });
      newestMessage.current = latest;
    }
  }, [messagesData, selected, messageVirtual]);
  useEffect(() => {
    setText("");
    setFile(null);
  }, [selected]);
  async function send() {
    if ((!text.trim() && !file) || !selected || action.isPending || sendBusy.current) return;
    sendBusy.current = true;
    try {
    if (file) {
      const body = new FormData();
      body.append("image", file);
      await action.mutateAsync({ path: `/chats/${selected}/image`, body });
      setFile(null);
    }
    if (text.trim()) {
      if (!pendingSend.current || pendingSend.current.chat !== selected || pendingSend.current.text !== text) {
        pendingSend.current = { chat: selected, text, key: requestId() };
      }
      await action.mutateAsync({
        path: `/chats/${selected}/messages`,
        body: { text, idempotency_key: pendingSend.current.key },
      });
      pendingSend.current = null;
      setText("");
    }
    } finally {
      sendBusy.current = false;
    }
  }
  async function openChat(id: number) {
    select(id);
    setLoadingChatId(id);
    sound("click");
    try {
      await action.mutateAsync({ path: `/chats/${id}/refresh` });
    } finally {
      setLoadingChatId(null);
    }
  }
  if (!selectedAccount) {
    return (
      <>
        <PageHead
          eyebrow="Переписка / 03"
          title="Входящие"
          text="Выберите аккаунт, сообщения которого хотите прочитать."
        />
        <section className="panel inbox-choice">
          <h2>Какой аккаунт открыть?</h2>
          <DataGuard query={accounts}>
            <div className="inbox-choice-grid">
              {accounts.data?.map((account) => (
                <button
                  key={account.id}
                  className="inbox-choice-card"
                  onClick={() => {
                    setSelectedAccount(String(account.id));
                    selectAccount(String(account.id));
                    select(null);
                  }}
                >
                  <Avatar
                    name={account.username}
                    url={account.avatar_url}
                    path={`/avatars/account/${account.id}`}
                  />
                  <span>
                    <strong>{account.username}</strong>
                    <small>Открыть входящие</small>
                  </span>
                  <ArrowUpRight size={17} />
                </button>
              ))}
            </div>
            {!accounts.data?.length && (
              <Empty
                title="Нет подключённых аккаунтов"
                text="Добавьте аккаунт FunPay, чтобы открыть входящие."
                action={<AddAccount />}
              />
            )}
          </DataGuard>
        </section>
      </>
    );
  }
  return (
    <>
      {!embedded && (
        <PageHead
          eyebrow="Коммуникации / 03"
          title="Входящие"
          text="Каждый разговор — на своём месте."
        >
          <select
            aria-label="Аккаунт входящих"
            value={selectedAccount}
            onChange={(e) => {
              setSelectedAccount(e.target.value);
              select(null);
            }}
          >
            <option value="all">Все аккаунты</option>
            {accounts.data?.map((a) => (
              <option key={a.id} value={a.id}>
                {a.username}
              </option>
            ))}
          </select>
          <Button
            secondary
            disabled={action.isPending || !accounts.data?.length}
            onClick={() => {
              if (selectedAccount === "all") {
                for (const a of accounts.data || [])
                  action.mutate({ path: `/accounts/${a.id}/sync-chats` });
              } else
                action.mutate({
                  path: `/accounts/${selectedAccount}/sync-chats`,
                });
            }}
          >
            <RefreshCw size={15} />
            Синхронизировать
          </Button>
        </PageHead>
      )}
      <section className={`inbox panel ${chat ? "has-chat" : ""}`}>
        <div className="conversation-list">
          {embedded && (
            <div className="ws-toolbar">
              {!accountId && (
                <select
                  aria-label="Аккаунт входящих"
                  value={selectedAccount}
                  onChange={(e) => {
                    setSelectedAccount(e.target.value);
                    select(null);
                  }}
                >
                  <option value="all">Все аккаунты</option>
                  {accounts.data?.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.username}
                    </option>
                  ))}
                </select>
              )}
              <Button
                secondary
                disabled={action.isPending}
                onClick={() => {
                  for (const a of (accounts.data || []).filter(
                    (a) =>
                      selectedAccount === "all" ||
                      String(a.id) === selectedAccount,
                  ))
                    action.mutate({ path: `/accounts/${a.id}/sync-chats` });
                }}
              >
                <RefreshCw size={14} />
                Обновить
              </Button>
            </div>
          )}
          <div className="inbox-search">
            <Search size={15} />
            <input
              aria-label="Поиск диалогов"
              placeholder="Найти диалог…"
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </div>
          <div className="segmented inbox-filters">
            {[
              ["all", "Все"],
              ["unread", "Новые"],
              ["order", "С заказом"],
            ].map(([value, label]) => (
              <button
                key={value}
                className={filter === value ? "active" : ""}
                onClick={() => setFilter(value)}
              >
                {label}
              </button>
            ))}
          </div>
          <div className="inbox-date-filters">
            <select
              aria-label="Порядок переписок"
              value={dateSort}
              onChange={(event) => setDateSort(event.target.value)}
            >
              <option value="newest">Сначала новые</option>
              <option value="oldest">Сначала старые</option>
            </select>
            <select
              aria-label="Период переписок"
              value={period}
              onChange={(event) => setPeriod(event.target.value)}
            >
              <option value="all">Все даты</option>
              <option value="today">Сегодня</option>
              <option value="week">7 дней</option>
              <option value="month">30 дней</option>
            </select>
          </div>
          <DataGuard query={chats}>
            <div ref={listRef} className="conversation-scroll">
              {chats.data?.length ? (
                <div
                  style={{
                    height: virtual.getTotalSize(),
                    position: "relative",
                  }}
                >
                  {virtual.getVirtualItems().map((item) => {
                    const row = chats.data![item.index];
                    return (
                      <button
                        key={row.id}
                        className={`conversation ${selected === row.id ? "selected" : ""}`}
                        style={{
                          position: "absolute",
                          top: item.start,
                          height: item.size,
                        }}
                        onClick={() => {
                          void openChat(row.id).catch(() => undefined);
                          if (row.unread_count)
                            action.mutate({
                              path: `/chats/${row.id}`,
                              method: "PATCH",
                              body: { unread_count: 0 },
                            });
                        }}
                      >
                        <Avatar
                          name={row.interlocutor_name}
                          url={row.interlocutor_avatar}
                          path={`/avatars/chat/${row.id}`}
                          small
                        />
                        <div>
                          <strong>
                            {row.is_pinned && <Pin size={11} />}{" "}
                            {row.interlocutor_name}
                          </strong>
                          <p>{row.last_message_text || "Изображение"}</p>
                          <small>
                            {accounts.data?.find((a) => a.id === row.account_id)
                              ?.username || `Аккаунт ${row.account_id}`}
                          </small>
                        </div>
                        <span
                          className="conversation-time"
                          title={date(row.last_message_at)}
                        >
                          {conversationDate(row.last_message_at)}
                          {row.unread_count > 0 && <b>{row.unread_count}</b>}
                        </span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                <Empty
                  title="Диалогов пока нет"
                  text="Синхронизируйте чаты подключённого аккаунта."
                />
              )}
            </div>
          </DataGuard>
        </div>
        {chat ? (
          <>
            <div className="chat-main">
              <div className="chat-header">
                <button
                  className="icon-button mobile-back"
                  onClick={() => select(null)}
                >
                  <ChevronDown size={18} />
                </button>
                <Avatar
                  name={chat.interlocutor_name}
                  url={chat.interlocutor_avatar}
                  path={`/avatars/chat/${chat.id}`}
                  small
                />
                <div>
                  <strong>{chat.interlocutor_name}</strong>
                  <small>
                    FunPay /{" "}
                    {
                      accounts.data?.find((a) => a.id === chat.account_id)
                        ?.username
                    }
                  </small>
                </div>
                <External
                  href={`https://funpay.com/users/${chat.interlocutor_id}/`}
                >
                  Профиль
                </External>
                <button
                  className="icon-button"
                  title="Закрепить диалог"
                  onClick={() =>
                    action.mutate({
                      path: `/chats/${chat.id}`,
                      method: "PATCH",
                      body: { is_pinned: !chat.is_pinned },
                    })
                  }
                >
                  <Pin size={16} />
                </button>
              </div>
              {(loadingChatId === chat.id || messages.isFetching) && (
                <div className="chat-loading">
                  <LoaderCircle size={14} className="spin" /> Загрузка
                  сообщений…
                </div>
              )}
              <div className="message-scroll" ref={messageRef}>
                <DataGuard query={messages}>
                  {messages.hasNextPage && (
                    <Button
                      secondary
                      disabled={messages.isFetchingNextPage}
                      onClick={() => {
                        void messages.fetchNextPage();
                      }}
                    >
                      Загрузить предыдущие сообщения
                    </Button>
                  )}
                  <div
                    style={{
                      height: messageVirtual.getTotalSize(),
                      position: "relative",
                    }}
                  >
                    {messageVirtual.getVirtualItems().map((item) => {
                      const message = messagesData[item.index];
                      return (
                        <div
                          key={message.id}
                          data-index={item.index}
                          ref={messageVirtual.measureElement}
                          style={{
                            position: "absolute",
                            top: 0,
                            left: 0,
                            width: "100%",
                            transform: `translateY(${item.start}px)`,
                          }}
                        >
                          <div
                            className={`message ${message.is_my ? "mine" : ""}`}
                          >
                            <div className="message-bubble">
                              {message.has_image &&
                              message.image_url &&
                              /^https:\/\/(?:[a-z0-9-]+\.)*funpay\.(?:com|ru)\//i.test(
                                message.image_url,
                              ) ? (
                                <ZoomImage src={message.image_url} />
                              ) : null}
                              <p>{message.text}</p>
                              <small>
                                {date(message.created_at)}{" "}
                                {message.is_my && <CheckCheck size={12} />}
                              </small>
                            </div>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </DataGuard>
              </div>
              <div className="composer">
                {preview && (
                  <div className="attachment-preview">
                    <img src={preview} alt="Перед отправкой" />
                    <button
                      className="icon-button"
                      title="Убрать вложение"
                      onClick={() => setFile(null)}
                    >
                      <X size={14} />
                    </button>
                  </div>
                )}
                <textarea
                  aria-label="Сообщение"
                  placeholder="Написать сообщение…"
                  value={text}
                  onChange={(event) => {
                    setText(event.target.value);
                    event.target.style.height = "auto";
                    event.target.style.height = `${Math.min(event.target.scrollHeight, 180)}px`;
                  }}
                  onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey) {
                      event.preventDefault();
                      void send().catch(() => undefined);
                    }
                  }}
                />
                <div className="composer-actions">
                  <input
                    ref={input}
                    hidden
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    onChange={(event) => {
                      const value = event.target.files?.[0];
                      if (value && value.size > 8 * 1024 * 1024)
                        toast.error("Максимум 8 МБ");
                      else setFile(value || null);
                    }}
                  />
                  <button
                    className="icon-button"
                    title="Прикрепить изображение"
                    onClick={() => input.current?.click()}
                  >
                    <ImagePlus size={18} />
                  </button>
                  <select
                    aria-label="Быстрый ответ"
                    value=""
                    onChange={(event) => setText(event.target.value)}
                  >
                    <option value="">Быстрый ответ</option>
                    <option>Здравствуйте! Товар в наличии.</option>
                    <option>
                      Спасибо за покупку! Приятного использования.
                    </option>
                    <option>Одну минуту, проверяю ваш заказ.</option>
                  </select>
                  <span>Enter — отправить</span>
                  <Button
                    disabled={action.isPending || (!text.trim() && !file)}
                    onClick={() => {
                      void send().catch(() => undefined);
                    }}
                  >
                    {action.isPending ? (
                      <LoaderCircle className="spin" size={16} />
                    ) : (
                      <Send size={16} />
                    )}
                  </Button>
                </div>
              </div>
            </div>
            <aside className="order-detail">
              <span className="eyebrow">Покупатель</span>
              <h3>{chat.interlocutor_name}</h3>
              <External
                href={`https://funpay.com/users/${chat.interlocutor_id}/`}
              >
                Открыть на FunPay
              </External>
              <hr />
              <span className="eyebrow">Текущий заказ</span>
              {order ? (
                <>
                  <h3>#{order.funpay_order_id}</h3>
                  <p>{order.description}</p>
                  <strong className="order-sum">
                    {money(order.sum, order.currency)}
                  </strong>
                  <Status value={order.status} />
                  <External
                    href={`https://funpay.com/orders/${order.funpay_order_id}/`}
                  >
                    Действия с заказом
                  </External>
                </>
              ) : (
                <p className="muted">Активный заказ не связан с диалогом.</p>
              )}
              <hr />
              <Field label="Заметка о покупателе">
                <textarea
                  value={notes}
                  onChange={(event) => setNotes(event.target.value)}
                  onBlur={() => {
                    if (notes !== chat.notes)
                      action.mutate({
                        path: `/chats/${chat.id}`,
                        method: "PATCH",
                        body: { notes },
                      });
                  }}
                  placeholder="Видна только вам"
                />
              </Field>
              <Confirm
                trigger={
                  <Button secondary disabled={!chat.interlocutor_id}>
                    В чёрный список
                  </Button>
                }
                title="Заблокировать автоматизацию?"
                text="Автоответчик и выдача для этого покупателя будут отключены."
                onConfirm={() =>
                  action.mutateAsync({
                    path: "/blacklist",
                    body: {
                      user_id: chat.interlocutor_id,
                      username: chat.interlocutor_name,
                      reason: "Из диалога",
                      scope: "chat",
                      account_id: chat.account_id,
                      chat_id: chat.id,
                    },
                    success: "Покупатель добавлен",
                  })
                }
              />
            </aside>
          </>
        ) : (
          <div className="chat-placeholder">
            <div className="chat-art">
              <Send size={36} />
            </div>
            <h2>Разговор начинается здесь</h2>
            <p>
              Выберите диалог, чтобы увидеть историю
              <br />и ответить покупателю.
            </p>
            <span className="keyboard-hint">
              Все ваши магазины в одном окне
            </span>
          </div>
        )}
      </section>
    </>
  );
}

export function DeliveryPage() {
  const selected = useAccountStore((s) => s.selected);
  const query = useData<Rule[]>("rules", accountQuery("/rules", selected));
  const accounts = useData<Account[]>("accounts", "/accounts");
  const action = useAction();
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("pool_keys");
  const [pool, setPool] = useState("");
  return (
    <>
      <PageHead
        eyebrow="Автоматизация / 05"
        title="Автовыдача"
        text="Товар отправляется после обнаружения нового оплаченного заказа."
      >
        <Modal
          open={open}
          onOpenChange={setOpen}
          trigger={
            <Button>
              <Plus size={16} />
              Создать правило
            </Button>
          }
          title="Правило выдачи"
          description="Совпадение по названию товара. Первичная история заказов не выдаётся автоматически."
        >
          <Form
            onSubmit={async (form) => {
              await action.mutateAsync({
                path: "/rules",
                body: {
                  account_id: Number(form.get("account_id")),
                  lot_title_pattern: form.get("pattern"),
                  match_regex: form.get("regex") === "on",
                  delivery_type: kind,
                  keys_pool: pool,
                  message_template: form.get("template"),
                  is_active: true,
                },
                success: "Правило создано",
              });
              setOpen(false);
              setPool("");
            }}
          >
            <Field label="Аккаунт">
              <AccountSelect accounts={accounts.data || []} />
            </Field>
            <Field label="Название товара или регулярное выражение">
              <input
                name="pattern"
                required
                maxLength={255}
                placeholder="Точное название предложения"
              />
            </Field>
            <label className="checkbox-field">
              <input name="regex" type="checkbox" />
              Использовать регулярное выражение
            </label>
            <Field label="Способ выдачи">
              <select
                value={kind}
                onChange={(event) => setKind(event.target.value)}
              >
                <option value="pool_keys">Пул ключей</option>
                <option value="static_text">Статический текст</option>
              </select>
            </Field>
            {kind === "pool_keys" && (
              <Field
                label={`Ключи · ${pool.split("\n").filter((line) => line.trim()).length} шт.`}
                hint="Один ключ на строку. Выданный ключ удаляется из пула."
              >
                <textarea
                  value={pool}
                  onChange={(event) => setPool(event.target.value)}
                  required
                  placeholder="SAMPLE-KEY-001"
                />
              </Field>
            )}
            <Field
              label="Сообщение покупателю"
              hint="Переменные: {item}, {order_id}, {buyer}, {date}"
            >
              <textarea
                name="template"
                required
                defaultValue="Спасибо за покупку, {buyer}! Ваш товар: {item}"
              />
            </Field>
          </Form>
        </Modal>
      </PageHead>
      <DataGuard query={query}>
        {query.data?.length ? (
          <div className="rule-grid">
            {query.data.map((rule) => (
              <article className="panel rule-card" key={rule.id}>
                <div className="rule-top">
                  <span className="event-icon">
                    <KeyRound size={19} />
                  </span>
                  <Toggle
                    checked={rule.is_active}
                    label="Включить правило"
                    onChange={() =>
                      action.mutate({ path: `/rules/${rule.id}/toggle` })
                    }
                  />
                </div>
                <small className="eyebrow">
                  {accounts.data?.find((a) => a.id === rule.account_id)
                    ?.username || `Аккаунт ${rule.account_id}`}
                </small>
                <h2>{rule.lot_title_pattern}</h2>
                <p>
                  {rule.delivery_type === "pool_keys"
                    ? "Выдача из пула"
                    : "Статическое сообщение"}
                  {rule.match_regex && " · Regex"}
                </p>
                <pre>{rule.message_template}</pre>
                {rule.delivery_type === "pool_keys" && (
                  <Modal
                    trigger={<Button secondary>Пополнить ключи</Button>}
                    title="Добавить ключи"
                    description="Текущий остаток сохранится. Повторяющиеся ключи отклоняются."
                  >
                    <Form
                      onSubmit={(data) =>
                        action.mutateAsync({
                          path: `/rules/${rule.id}/keys`,
                          body: { keys: data.get("keys") },
                          success: "Пул пополнен",
                        })
                      }
                    >
                      <Field label="Один ключ на строку">
                        <textarea name="keys" required rows={7} />
                      </Field>
                    </Form>
                  </Modal>
                )}
                <div className="rule-footer">
                  <span
                    className={
                      rule.remaining < 3 && rule.delivery_type === "pool_keys"
                        ? "text-amber"
                        : "text-green"
                    }
                  >
                    {rule.delivery_type === "pool_keys"
                      ? `${rule.remaining} ключей в наличии`
                      : "Без ограничения остатка"}
                  </span>
                  <Confirm
                    trigger={
                      <button
                        className="icon-button danger"
                        title="Удалить правило"
                      >
                        <Trash2 size={15} />
                      </button>
                    }
                    title="Удалить правило?"
                    text="Оставшиеся ключи этого правила будут удалены."
                    onConfirm={() =>
                      action.mutateAsync({
                        path: `/rules/${rule.id}`,
                        method: "DELETE",
                      })
                    }
                  />
                </div>
              </article>
            ))}
          </div>
        ) : (
          <section className="panel">
            <Empty
              title="Магазин может работать сам"
              text="Создайте правило выдачи ключей или постоянного сообщения покупателю."
            />
          </section>
        )}
      </DataGuard>
      <div className="note">
        <ShieldCheck size={17} />
        <span>
          При неизвестном результате отправки повтор блокируется. Проверьте чат
          и журнал перед ручной выдачей.
        </span>
      </div>
    </>
  );
}

export function ProxiesPage() {
  const query = useData<ProxyServer[]>("proxies", "/proxies");
  const accounts = useData<Account[]>("accounts", "/accounts");
  const action = useAction();
  const [open, setOpen] = useState(false);
  return (
    <>
      <PageHead
        eyebrow="Инфраструктура / 06"
        title="Прокси"
        text="Контроль соединений и изоляция аккаунтов."
      >
        <Button
          secondary
          disabled={action.isPending}
          onClick={() =>
            action.mutate({
              path: "/proxies/check",
              success: "Проверка завершена",
            })
          }
        >
          <RefreshCw size={15} />
          Проверить все
        </Button>
        <Modal
          open={open}
          onOpenChange={setOpen}
          trigger={
            <Button>
              <Plus size={16} />
              Добавить прокси
            </Button>
          }
          title="Массовое добавление"
          description="До 100 адресов, каждый с новой строки. Пароли сохраняются зашифрованными."
        >
          <Form
            onSubmit={async (form) => {
              await action.mutateAsync({
                path: "/proxies",
                body: { lines: form.get("lines") },
                success: "Прокси добавлены",
              });
              setOpen(false);
            }}
          >
            <Field
              label="Адреса прокси"
              hint="http://user:password@host:port · socks5://host:port · host:port:user:password"
            >
              <textarea
                name="lines"
                required
                rows={8}
                placeholder="http://127.0.0.1:8080"
              />
            </Field>
          </Form>
        </Modal>
      </PageHead>
      <section className="panel">
        <DataGuard query={query}>
          {query.data?.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Сервер</th>
                    <th>Протокол</th>
                    <th>Задержка</th>
                    <th>Аккаунты</th>
                    <th>Статус</th>
                    <th>Проверен</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {query.data.map((proxy) => (
                    <tr key={proxy.id}>
                      <td className="mono">
                        {proxy.host}:{proxy.port}
                      </td>
                      <td>
                        <span className="tag">
                          {proxy.protocol.toUpperCase()}
                        </span>
                      </td>
                      <td
                        className={`mono ${proxy.ping_ms === null ? "muted" : proxy.ping_ms < 100 ? "text-green" : proxy.ping_ms < 300 ? "text-amber" : "text-red"}`}
                      >
                        {proxy.ping_ms === null ? "—" : `${proxy.ping_ms} ms`}
                      </td>
                      <td>
                        {accounts.data?.filter((a) => a.proxy_id === proxy.id)
                          .length || 0}
                      </td>
                      <td>
                        <Status value={proxy.status} />
                      </td>
                      <td className="muted">{date(proxy.last_checked_at)}</td>
                      <td>
                        <Confirm
                          trigger={
                            <button
                              className="icon-button danger"
                              title="Удалить прокси"
                            >
                              <Trash2 size={15} />
                            </button>
                          }
                          title="Удалить прокси?"
                          text="Связанные аккаунты перейдут на прямое соединение."
                          onConfirm={() =>
                            action.mutateAsync({
                              path: `/proxies/${proxy.id}`,
                              method: "DELETE",
                            })
                          }
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="Прямое соединение"
              text="Добавьте прокси, если для аккаунтов нужны отдельные адреса."
            />
          )}
        </DataGuard>
      </section>
    </>
  );
}

export function BlacklistPage() {
  const query = useData<Blacklist[]>("blacklist", "/blacklist");
  const accounts = useData<Account[]>("accounts", "/accounts");
  const action = useAction();
  const [open, setOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importAccount, setImportAccount] = useState("");
  const [importScope, setImportScope] = useState<"global" | "account">("global");
  const [scope, setScope] = useState<"global" | "account">("global");
  const [scopeAccount, setScopeAccount] = useState("");
  const [chosen, setChosen] = useState<number[]>([]);
  const candidates = useData<
    {
      chat_id: number;
      user_id: number;
      username: string;
      avatar_url: string | null;
    }[]
  >(
    "blacklist-candidates",
    `/blacklist/candidates?account_id=${importAccount}`,
    importOpen && Boolean(importAccount),
  );
  return (
    <>
      <PageHead
        eyebrow="Защита / 07"
        title="Чёрный список"
        text="Исключение можно применить ко всем аккаунтам или только к выбранному аккаунту либо диалогу."
      >
        <Modal
          open={importOpen}
          onOpenChange={setImportOpen}
          trigger={<Button secondary>Импорт из переписки FunPay</Button>}
          title="Выбрать покупателей"
          description="Загрузите диалоги аккаунта и отметьте людей для чёрного списка."
        >
          <Field label="Аккаунт">
            <select
              value={importAccount}
              onChange={(e) => {
                setImportAccount(e.target.value);
                setChosen([]);
              }}
            >
              <option value="">Выберите аккаунт</option>
              {accounts.data?.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.username}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Действие исключения">
            <select value={importScope} onChange={(e) => setImportScope(e.target.value as "global" | "account")}>
              <option value="global">Все аккаунты</option>
              <option value="account">Только выбранный аккаунт</option>
            </select>
          </Field>
          {importAccount && (
            <Button
              secondary
              disabled={action.isPending}
              onClick={async () => {
                await action.mutateAsync({
                  path: `/accounts/${importAccount}/sync-chats`,
                });
                await candidates.refetch();
              }}
            >
              Загрузить диалоги с FunPay
            </Button>
          )}
          <div className="blacklist-candidates">
            {candidates.data?.map((person) => (
              <label key={person.chat_id}>
                <input
                  type="checkbox"
                  checked={chosen.includes(person.chat_id)}
                  onChange={(e) =>
                    setChosen(
                      e.target.checked
                        ? [...chosen, person.chat_id]
                        : chosen.filter((id) => id !== person.chat_id),
                    )
                  }
                />
                <Avatar
                  name={person.username}
                  url={person.avatar_url}
                  path={`/avatars/chat/${person.chat_id}`}
                  small
                />
                <span>
                  {person.username} · #{person.user_id}
                </span>
              </label>
            ))}
          </div>
          {importAccount && !candidates.data?.length && (
            <p className="muted">
              Диалогов пока нет. Сначала загрузите их с FunPay.
            </p>
          )}
          <Button
            disabled={!chosen.length || action.isPending}
            onClick={async () => {
              await action.mutateAsync({
                path: "/blacklist/import",
                body: { account_id: Number(importAccount), chat_ids: chosen, scope: importScope },
                success: "Покупатели добавлены",
              });
              setImportOpen(false);
              setChosen([]);
            }}
          >
            Добавить выбранных ({chosen.length})
          </Button>
        </Modal>
        <Modal
          open={open}
          onOpenChange={setOpen}
          trigger={
            <Button>
              <Plus size={16} />
              Добавить покупателя
            </Button>
          }
          title="Исключение покупателя"
          description="Для конкретного диалога используйте кнопку в окне входящих."
        >
          <Form
            onSubmit={async (form) => {
              await action.mutateAsync({
                path: "/blacklist",
                body: {
                  user_id: Number(form.get("user_id")),
                  username: form.get("username"),
                  reason: form.get("reason"),
                  scope,
                  ...(scope === "account" ? { account_id: Number(scopeAccount) } : {}),
                },
                success: "Покупатель добавлен",
              });
              setOpen(false);
            }}
          >
            <Field label="ID FunPay">
              <input name="user_id" type="number" min={1} required />
            </Field>
            <Field label="Имя">
              <input name="username" required maxLength={100} />
            </Field>
            <Field label="Причина">
              <textarea name="reason" />
            </Field>
            <Field label="Действие исключения">
              <select value={scope} onChange={(e) => setScope(e.target.value as "global" | "account")}>
                <option value="global">Все аккаунты</option>
                <option value="account">Только выбранный аккаунт</option>
              </select>
            </Field>
            {scope === "account" && (
              <Field label="Аккаунт">
                <select value={scopeAccount} onChange={(e) => setScopeAccount(e.target.value)} required>
                  <option value="">Выберите аккаунт</option>
                  {accounts.data?.map((account) => (
                    <option key={account.id} value={account.id}>{account.username}</option>
                  ))}
                </select>
              </Field>
            )}
          </Form>
        </Modal>
      </PageHead>
      <section className="panel">
        <DataGuard query={query}>
          {query.data?.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Покупатель</th>
                    <th>Область</th>
                    <th>Причина</th>
                    <th>Добавлен</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {query.data.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <External
                          href={`https://funpay.com/users/${row.user_id}/`}
                        >
                          {row.username} · #{row.user_id}
                        </External>
                      </td>
                      <td>
                        {row.scope === "global"
                          ? "Все аккаунты"
                          : row.scope === "account"
                            ? "Аккаунт #" + row.account_id
                            : "Диалог #" + row.chat_id}
                      </td>
                      <td>{row.reason || "—"}</td>
                      <td>{date(row.created_at)}</td>
                      <td>
                        <Button
                          secondary
                          onClick={() =>
                            action.mutate({
                              path: `/blacklist/${row.id}`,
                              method: "DELETE",
                              success: "Исключение удалено",
                            })
                          }
                        >
                          Убрать
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="Исключений нет"
              text="Покупателей можно добавить вручную или из окна диалога."
            />
          )}
        </DataGuard>
      </section>
    </>
  );
}

function DeliveryRecovery({ order }: { order: Order }) {
  const [open, setOpen] = useState(false);
  const query = useData<{ state: string; text: string }>(
    "reserved-delivery",
    `/orders/${order.id}/delivery`,
    open,
  );
  const action = useAction();
  return (
    <Modal
      open={open}
      onOpenChange={setOpen}
      trigger={<Button secondary>Проверить выдачу</Button>}
      title={`Выдача #${order.funpay_order_id}`}
      description="Сравните зарезервированный товар с сообщениями в чате FunPay. При необходимости завершите выдачу вручную."
    >
      <DataGuard query={query}>
        <Field label="Зарезервированное сообщение">
          <textarea
            readOnly
            rows={7}
            value={query.data?.text || "Текст не сохранён"}
            onFocus={(event) => event.target.select()}
          />
        </Field>
        <External href={`https://funpay.com/orders/${order.funpay_order_id}/`}>
          Открыть заказ и чат
        </External>
        <div className="form-actions">
          <Confirm
            trigger={<Button disabled={action.isPending}>Товар выдан</Button>}
            title="Подтвердить выдачу?"
            text="Подтвердите, что покупатель уже получил товар. Локальный статус изменится на выданный."
            onConfirm={async () => {
              await action.mutateAsync({
                path: `/orders/${order.id}/confirm-delivery`,
                success: "Выдача подтверждена",
              });
              setOpen(false);
            }}
          />
        </div>
      </DataGuard>
    </Modal>
  );
}

export function FinancialsPage() {
  const selected = useAccountStore((s) => s.selected);
  const [period, setPeriod] = useState("month");
  const query = useData<Order[]>(
    "finance-orders",
    accountQuery(`/orders?period=${period}`, selected),
  );
  const summary = useData<{
    totals: Record<"RUB" | "USD" | "EUR", string>;
    count: number;
  }>(
    "finance-summary",
    accountQuery(`/finance/summary?period=${period}`, selected),
  );
  const [filter, setFilter] = useState("");
  const rows =
    query.data?.filter((row) => !filter || row.status === filter) || [];
  return (
    <>
      <PageHead
        eyebrow="Отчётность / 08"
        title="Финансы"
        text="Заказы и выручка по данным синхронизации FunPay."
      >
        <select
          aria-label="Период выручки"
          value={period}
          onChange={(event) => setPeriod(event.target.value)}
        >
          <option value="hour">Последний час</option>
          <option value="week">Последняя неделя</option>
          <option value="month">Последние 30 дней</option>
          <option value="year">Последний год</option>
        </select>
      </PageHead>
      <div className="metrics finance-metrics">
        {(["RUB", "USD", "EUR"] as const).map((cur) => (
          <Metric
            key={cur}
            title={`Выручка · ${cur}`}
            value={money(summary.data?.totals[cur] || 0, cur)}
            icon={<Coins size={18} />}
            detail="По заказам с известной датой"
          />
        ))}
      </div>
      <section className="panel">
        <div className="table-toolbar">
          <h2>Журнал заказов</h2>
          <select
            aria-label="Статус заказа"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
          >
            <option value="">Все статусы</option>
            <option value="paid">Оплаченные</option>
            <option value="closed">Завершённые</option>
            <option value="refunded">Возвраты</option>
          </select>
        </div>
        <DataGuard query={query}>
          {rows.length ? (
            <div className="table-scroll">
              <table>
                <thead>
                  <tr>
                    <th>Заказ</th>
                    <th>Товар</th>
                    <th>Покупатель</th>
                    <th>Сумма</th>
                    <th>Статус</th>
                    <th>Выдача</th>
                    <th>Обнаружен</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row) => (
                    <tr key={row.id}>
                      <td>
                        <External
                          href={`https://funpay.com/orders/${row.funpay_order_id}/`}
                        >
                          #{row.funpay_order_id}
                        </External>
                      </td>
                      <td>{row.description}</td>
                      <td>{row.buyer_name}</td>
                      <td className="mono">{money(row.sum, row.currency)}</td>
                      <td>
                        <Status value={row.status} />
                      </td>
                      <td>
                        <span
                          className={
                            ["uncertain", "sending"].includes(
                              row.delivery_state,
                            )
                              ? "text-amber"
                              : "muted"
                          }
                        >
                          {row.auto_delivered
                            ? "Выдано"
                            : row.delivery_state === "uncertain"
                              ? "Нужна проверка чата"
                              : row.delivery_state === "historical"
                                ? "История"
                                : row.delivery_state === "sending"
                                  ? "Проверить отправку"
                                  : "Ожидание"}
                        </span>
                        {["uncertain", "sending"].includes(
                          row.delivery_state,
                        ) && (
                          <div style={{ marginTop: 8 }}>
                            <DeliveryRecovery order={row} />
                          </div>
                        )}
                      </td>
                      <td className="muted">{date(row.created_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <Empty
              title="Нет заказов для отображения"
              text="Заказы появятся после подключения и синхронизации аккаунта."
            />
          )}
        </DataGuard>
      </section>
      <div className="note">
        <Clock3 size={17} />
        <span>
          Дата — время первого обнаружения заказа. Валюты учитываются отдельно;
          себестоимость и фактические комиссии по сделкам не загружаются.
        </span>
      </div>
    </>
  );
}

export function HealthPage() {
  const query = useData<Health>("health", "/health");
  const selected = useAccountStore((s) => s.selected);
  const logs = useData<Activity[]>("logs", accountQuery("/logs", selected));
  return (
    <>
      <PageHead
        eyebrow="Инфраструктура / 09"
        title="Здоровье системы"
        text="Состояние хранилища, фоновых задач и соединений."
      >
        <Button
          secondary
          onClick={() => {
            void query.refetch();
            void logs.refetch();
          }}
        >
          <RefreshCw size={15} />
          Обновить
        </Button>
      </PageHead>
      <DataGuard query={query}>
        {query.data && (
          <div className="metrics">
            <Metric
              title="Хранилище"
              value={query.data.database.toUpperCase()}
              icon={<ShieldCheck size={18} />}
              detail={`${query.data.database_ms} ms · SELECT 1`}
            />
            <Metric
              title="Воркеры аккаунтов"
              value={String(query.data.workers)}
              icon={<ActivityIcon size={18} />}
              detail="Активные фоновые задачи"
            />
            <Metric
              title="Планировщик"
              value={query.data.scheduler ? "Работает" : "Выключен"}
              icon={<Clock3 size={18} />}
              detail="APScheduler"
            />
            <Metric
              title="Подключения UI"
              value={String(query.data.subscribers)}
              icon={<Zap size={18} />}
              detail="WebSocket-подписки"
            />
          </div>
        )}
      </DataGuard>
      <section className="panel">
        <div className="panel-head">
          <h2>Системный журнал</h2>
          <span className="tag">Последние 200 событий</span>
        </div>
        <DataGuard query={logs}>
          <ActivityList rows={logs.data || []} />
        </DataGuard>
      </section>
    </>
  );
}

export function AutoRepliesPage({ section }: { section?: string }) {
  const query = useData<Plugins>("auto-replies", "/settings/plugins");
  const action = useAction();
  const [form, setForm] = useState<Plugins | null>(null);
  useEffect(() => {
    if (query.data)
      setForm((previous) =>
        previous && section
          ? {
              ...previous,
              responder: query.data.responder,
              auto_ticket: query.data.auto_ticket,
              auto_thanks: query.data.auto_thanks,
            }
          : {
              ...query.data,
              reply_delay_min: query.data.reply_delay_min ?? 2,
              reply_delay_max: query.data.reply_delay_max ?? 5,
              auto_ticket: query.data.auto_ticket ?? false,
              commands: query.data.commands || [],
              ticket_templates: query.data.ticket_templates || [],
              review_replies: query.data.review_replies || {},
            },
      );
  }, [query.data, section]);
  const update = (
    index: number,
    change: Partial<Plugins["commands"][number]>,
  ) => {
    if (!form) return;
    setForm({
      ...form,
      commands: form.commands.map((item, i) =>
        i === index ? { ...item, ...change } : item,
      ),
    });
  };
  return (
    <>
      <>
        {!section && (
          <PageHead
            eyebrow="Автоматизация"
            title="Автоответы"
            text="Ответы на команды в чате и отзывы по оценке."
          />
        )}
      </>
      <DataGuard query={query}>
        {form && (
          <>
            {(!section || section === "responder") && (
              <>
                {" "}
                <section className="panel reply-panel">
                  <div className="panel-head">
                    <div>
                      <h2>Ответы в переписке</h2>
                      <p className="muted">
                        Команды проверяются по порядку. Между ответами в одном
                        чате действует пауза 60 секунд.
                      </p>
                    </div>
                    {!section && (
                      <Toggle
                        label="Включить автоответы"
                        checked={form.responder}
                        onChange={(value) =>
                          setForm({ ...form, responder: value })
                        }
                      />
                    )}
                  </div>
                  <div className="reply-body">
                    <PluginPart section="messages">
                      <Field label="Первое сообщение">
                        <textarea
                          value={form.first_message}
                          onChange={(e) =>
                            setForm({ ...form, first_message: e.target.value })
                          }
                        />
                      </Field>
                      <h3>Команды и фразы</h3>
                      {form.commands.map((command, index) => (
                        <div className="reply-command" key={index}>
                          <div className="reply-command-top">
                            <strong>Команда {index + 1}</strong>
                            <Toggle
                              label="Активна"
                              checked={command.is_active}
                              onChange={(v) => update(index, { is_active: v })}
                            />
                            <button
                              className="icon-button danger"
                              title="Удалить команду"
                              onClick={() =>
                                setForm({
                                  ...form,
                                  commands: form.commands.filter(
                                    (_, i) => i !== index,
                                  ),
                                })
                              }
                            >
                              <Trash2 size={16} />
                            </button>
                          </div>
                          <div className="two-fields">
                            <Field label="Фраза или команда">
                              <input
                                value={command.trigger}
                                maxLength={100}
                                placeholder="/наличие"
                                onChange={(e) =>
                                  update(index, { trigger: e.target.value })
                                }
                              />
                            </Field>
                            <Field label="Совпадение">
                              <select
                                value={command.match}
                                onChange={(e) =>
                                  update(index, {
                                    match: e.target.value as
                                      | "exact"
                                      | "contains",
                                  })
                                }
                              >
                                <option value="exact">Точное</option>
                                <option value="contains">Содержит фразу</option>
                              </select>
                            </Field>
                          </div>
                          <Field label="Ответ">
                            <textarea
                              value={command.reply}
                              maxLength={5000}
                              onChange={(e) =>
                                update(index, { reply: e.target.value })
                              }
                            />
                          </Field>
                        </div>
                      ))}
                      <Button
                        secondary
                        onClick={() =>
                          setForm({
                            ...form,
                            commands: [
                              ...form.commands,
                              {
                                trigger: "",
                                reply: "",
                                match: "exact",
                                is_active: true,
                              },
                            ],
                          })
                        }
                      >
                        <Plus size={15} /> Добавить команду
                      </Button>
                    </PluginPart>
                    <div className="reply-advanced">
                      <h3>Правила ответов</h3>
                      <PluginPart section="settings">
                        <div className="two-fields">
                          <Field label="Пауза перед ответом, от (сек)">
                            <input
                              type="number"
                              min={0}
                              max={30}
                              step={0.5}
                              value={form.reply_delay_min}
                              onChange={(e) =>
                                setForm({
                                  ...form,
                                  reply_delay_min: Number(e.target.value),
                                })
                              }
                            />
                          </Field>
                          <Field label="До (сек)">
                            <input
                              type="number"
                              min={0}
                              max={60}
                              step={0.5}
                              value={form.reply_delay_max}
                              onChange={(e) =>
                                setForm({
                                  ...form,
                                  reply_delay_max: Number(e.target.value),
                                })
                              }
                            />
                          </Field>
                        </div>
                      </PluginPart>
                      <PluginPart section="settings">
                        <div className="two-fields">
                          <Field label="Ночь с (час)">
                            <input
                              type="number"
                              min={0}
                              max={23}
                              value={form.night_start}
                              onChange={(e) =>
                                setForm({
                                  ...form,
                                  night_start: Number(e.target.value),
                                })
                              }
                            />
                          </Field>
                          <Field label="До (час)">
                            <input
                              type="number"
                              min={0}
                              max={23}
                              value={form.night_end}
                              onChange={(e) =>
                                setForm({
                                  ...form,
                                  night_end: Number(e.target.value),
                                })
                              }
                            />
                          </Field>
                        </div>
                      </PluginPart>
                      <PluginPart section="messages">
                        <Field label="Ночной ответ">
                          <textarea
                            value={form.night_reply}
                            onChange={(e) =>
                              setForm({ ...form, night_reply: e.target.value })
                            }
                          />
                        </Field>
                      </PluginPart>
                    </div>
                  </div>
                </section>
              </>
            )}
            {(!section || section === "auto-ticket") && (
              <>
                {" "}
                <section className="panel reply-panel">
                  <div className="panel-head">
                    <div>
                      <h2>AutoTicket · Арбитраж</h2>
                      <p className="muted">
                        Распознаёт подтверждённую метку арбитража в чате.
                        Уведомление Telegram требует события «Споры» в
                        настройках.
                      </p>
                    </div>
                    {!section && (
                      <Toggle
                        label="Включить AutoTicket"
                        checked={form.auto_ticket}
                        onChange={(value) =>
                          setForm({ ...form, auto_ticket: value })
                        }
                      />
                    )}
                  </div>
                  <div className="reply-body">
                    {form.ticket_templates.map((rule, index) => (
                      <div className="reply-command" key={index}>
                        <div className="reply-command-top">
                          <strong>Шаблон {index + 1}</strong>
                          <Toggle
                            label="Активен"
                            checked={rule.is_active}
                            onChange={(value) =>
                              setForm({
                                ...form,
                                ticket_templates: form.ticket_templates.map(
                                  (r, i) =>
                                    i === index
                                      ? { ...r, is_active: value }
                                      : r,
                                ),
                              })
                            }
                          />
                          <button
                            className="icon-button danger"
                            title="Удалить шаблон"
                            onClick={() =>
                              setForm({
                                ...form,
                                ticket_templates: form.ticket_templates.filter(
                                  (_, i) => i !== index,
                                ),
                              })
                            }
                          >
                            <Trash2 size={16} />
                          </button>
                        </div>
                        <Field label="Регулярное выражение для текста арбитража">
                          <input
                            value={rule.pattern}
                            onChange={(e) =>
                              setForm({
                                ...form,
                                ticket_templates: form.ticket_templates.map(
                                  (r, i) =>
                                    i === index
                                      ? { ...r, pattern: e.target.value }
                                      : r,
                                ),
                              })
                            }
                          />
                        </Field>
                        <Field label="Ответ в чат">
                          <textarea
                            value={rule.reply}
                            onChange={(e) =>
                              setForm({
                                ...form,
                                ticket_templates: form.ticket_templates.map(
                                  (r, i) =>
                                    i === index
                                      ? { ...r, reply: e.target.value }
                                      : r,
                                ),
                              })
                            }
                          />
                        </Field>
                      </div>
                    ))}
                    <Button
                      secondary
                      onClick={() =>
                        setForm({
                          ...form,
                          ticket_templates: [
                            ...form.ticket_templates,
                            { pattern: "", reply: "", is_active: true },
                          ],
                        })
                      }
                    >
                      <Plus size={15} /> Добавить шаблон
                    </Button>
                    <p className="muted">
                      Ответ отправляется только при совпадении шаблона. Статус
                      «Печатает…» недоступен в подтверждённом протоколе FunPay.
                    </p>
                  </div>
                </section>
              </>
            )}
            {(!section || section === "reviews") && (
              <>
                {" "}
                <section className="panel reply-panel">
                  <div className="panel-head">
                    <div>
                      <h2>Ответы на отзывы</h2>
                      <p className="muted">
                        Текст публикуется под отзывом на FunPay. Пустые оценки
                        пропускаются.
                      </p>
                    </div>
                    {!section && (
                      <Toggle
                        label="Включить ответы на отзывы"
                        checked={form.auto_thanks}
                        onChange={(value) =>
                          setForm({ ...form, auto_thanks: value })
                        }
                      />
                    )}
                  </div>
                  <div className="reply-body review-grid">
                    {[1, 2, 3, 4, 5].map((rating) => (
                      <Field key={rating} label={"⭐".repeat(rating)}>
                        <textarea
                          placeholder={`Ответ на оценку ${rating} ★`}
                          maxLength={5000}
                          value={
                            form.review_replies[String(rating)] ??
                            (rating === 5 ? form.thanks_text : "")
                          }
                          onChange={(e) =>
                            setForm({
                              ...form,
                              review_replies: {
                                ...form.review_replies,
                                [rating]: e.target.value,
                              },
                              ...(rating === 5
                                ? { thanks_text: e.target.value }
                                : {}),
                            })
                          }
                        />
                      </Field>
                    ))}
                  </div>
                </section>
              </>
            )}
            <div className="sticky-actions">
              <span>Изменения применятся к новым сообщениям и отзывам</span>
              <Button
                disabled={
                  action.isPending ||
                  ((!section || section === "responder") &&
                    (form.reply_delay_min > form.reply_delay_max ||
                      form.commands.some(
                        (c) => !c.trigger.trim() || !c.reply.trim(),
                      ))) ||
                  ((!section || section === "auto-ticket") &&
                    form.ticket_templates.some(
                      (t) => !t.pattern.trim() || !t.reply.trim(),
                    ))
                }
                onClick={() =>
                  action.mutate({
                    path: section
                      ? `/plugins/${section}/settings`
                      : "/settings/plugins",
                    method: "PUT",
                    body: form,
                    success: "Автоответы сохранены",
                  })
                }
              >
                Сохранить изменения
              </Button>
            </div>
          </>
        )}
      </DataGuard>
    </>
  );
}

export function SettingsPage() {
  const query = useData<AppSettings>("settings", "/settings/app");
  const telegram = useData<Telegram>("telegram", "/settings/telegram");
  const action = useAction();
  const [form, setForm] = useState<AppSettings | null>(null);
  const [bot, setBot] = useState<Telegram | null>(null);
  const compact = useSettingsStore((s) => s.compact);
  const muted = useSettingsStore((s) => s.muted);
  const toggle = useSettingsStore((s) => s.toggle);
  useEffect(() => {
    if (query.data) setForm(old => old || query.data!);
  }, [query.data]);
  useEffect(() => {
    if (telegram.data) setBot(old => old || telegram.data!);
  }, [telegram.data]);
  const auto = useAutoSave({ value: form && { ...form, compact, sounds: !muted }, dirty: !!form && !!query.data && JSON.stringify(form) !== JSON.stringify(query.data), enabled: !!form,
    save: value => request("/settings/app", "PUT", value), queryKeys: ["settings"] });
  const botAuto = useAutoSave({ value: bot, dirty: !!bot && !!telegram.data && JSON.stringify(bot) !== JSON.stringify(telegram.data), enabled: !!bot,
    save: value => request("/settings/telegram", "PUT", value), queryKeys: ["telegram"] });
  return (
    <>
      <PageHead
        eyebrow="Параметры / 11"
        title="Настройки"
        text="Рабочая среда, расчёты и уведомления."
      />
      <section className="panel control-links">
        <div className="panel-head">
          <h2>Основное управление</h2>
        </div>
        <div className="control-links-grid">
          <a href="#appearance">
            Внешний вид <ArrowUpRight size={15} />
          </a>
          <Link to="/lots">
            Лоты и автоподнятие <ArrowUpRight size={15} />
          </Link>
          <Link to="/auto-replies">
            Автоответы <ArrowUpRight size={15} />
          </Link>
          <Link to="/auto-delivery">
            Автовыдача <ArrowUpRight size={15} />
          </Link>
          <Link to="/loyalty">
            Бонусы и акции <ArrowUpRight size={15} />
          </Link>
        </div>
      </section>
      <AppearanceSettings />
      <div className="settings-grid">
        <section className="panel settings-panel">
          <div className="panel-head">
            <h2>Рабочее пространство</h2>
          </div>
          <div className="settings-body">
            <div className="setting-row">
              <div>
                <strong>Компактный интерфейс</strong>
                <p>Меньше отступов, больше данных</p>
              </div>
              <Toggle
                label="Компактный интерфейс"
                checked={compact}
                onChange={() => toggle("compact")}
              />
            </div>
            <div className="setting-row">
              <div>
                <strong>Звуки событий</strong>
                <p>Сообщения, заказы и предупреждения</p>
              </div>
              <Toggle
                label="Звуки событий"
                checked={!muted}
                onChange={() => toggle("muted")}
              />
            </div>
            <div className="setting-row">
              <div>
                <strong>Перенос на VPS</strong>
                <p>Локальные данные и ключи через SSH</p>
              </div>
              <Modal
                trigger={<Button secondary>Команда переноса</Button>}
                title="Перенос на свой VPS"
                description="Перед переносом закройте локальное приложение. Нужны SSH и sudo на Ubuntu/Debian VPS."
              >
                <Field label="Команда PowerShell">
                  <input
                    readOnly
                    value={
                      "powershell -File .\\deploy\\push-vps.ps1 -Server SERVER -User USER"
                    }
                    onFocus={(event) => event.target.select()}
                  />
                </Field>
                <p className="muted">
                  Для публичного HTTPS добавьте -Domain example.com после
                  настройки DNS. Без домена используйте SSH-туннель.
                </p>
              </Modal>
            </div>
            <DataGuard query={query}>
              {form && (
                <>
                  <Field label="Часовой пояс">
                    <input
                      value={form.timezone}
                      onChange={(e) =>
                        setForm({ ...form, timezone: e.target.value })
                      }
                    />
                  </Field>
                  <Field
                    label="Расчётная комиссия FunPay, %"
                    hint="Это оценка для аналитики, а не фактическая комиссия по каждому заказу."
                  >
                    <input
                      type="number"
                      min={0}
                      max={100}
                      step={0.1}
                      value={form.commission_percent}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          commission_percent: Number(e.target.value),
                        })
                      }
                    />
                  </Field>
                  <Field
                    label="Интервал автоподнятия, секунд"
                    hint="При ответе о кулдауне используется время ожидания FunPay."
                  >
                    <input
                      type="number"
                      min={60}
                      max={86400}
                      value={form.auto_raise_interval}
                      onChange={(e) =>
                        setForm({
                          ...form,
                          auto_raise_interval: Number(e.target.value),
                        })
                      }
                    />
                  </Field>
                  <AutoSaveStatus state={auto} />
                </>
              )}
            </DataGuard>
          </div>
        </section>
        <section className="panel settings-panel">
          <div className="panel-head">
            <h2>Telegram</h2>
            <span className="tag">Уведомления + ответы</span>
          </div>
          <div className="settings-body">
            <DataGuard query={telegram}>
              {bot && (
                <>
                  <div className="setting-row">
                    <div>
                      <strong>Подключить уведомления</strong>
                      <p>Ответ на уведомление отправляется в чат FunPay</p>
                    </div>
                    <Toggle
                      label="Уведомления Telegram"
                      checked={bot.enabled}
                      onChange={(value) => setBot({ ...bot, enabled: value })}
                    />
                  </div>
                  <Field
                    label="Bot Token"
                    hint={
                      bot.has_token
                        ? "Токен сохранён. Оставьте пустым, чтобы сохранить текущий."
                        : "Получите токен у BotFather."
                    }
                  >
                    <input
                      type="password"
                      autoComplete="off"
                      value={bot.token}
                      onChange={(e) =>
                        setBot({ ...bot, token: e.target.value })
                      }
                      placeholder={
                        bot.has_token ? "••••••••••••••••" : "123456:ABC…"
                      }
                    />
                  </Field>
                  <Field label="Chat ID">
                    <input
                      value={bot.chat_id}
                      onChange={(e) =>
                        setBot({ ...bot, chat_id: e.target.value })
                      }
                      placeholder="Например, 123456789"
                    />
                  </Field>
                  <div className="checkbox-list">
                    {[
                      ["orders", "Новые оплаченные заказы"],
                      ["chats", "Сообщения покупателей"],
                      ["tickets", "Споры и арбитраж"],
                      ["lots", "Поднятие лотов"],
                      ["stock", "Заканчиваются ключи"],
                      ["errors", "Ошибки сессий и прокси"],
                    ].map(([value, label]) => (
                      <label className="checkbox-field" key={value}>
                        <input
                          type="checkbox"
                          checked={bot.events.includes(value)}
                          onChange={(e) =>
                            setBot({
                              ...bot,
                              events: e.target.checked
                                ? [...bot.events, value]
                                : bot.events.filter((item) => item !== value),
                            })
                          }
                        />
                        {label}
                      </label>
                    ))}
                  </div>
                  <div className="head-actions">
                    <AutoSaveStatus state={botAuto} />
                    <Button
                      secondary
                      disabled={action.isPending}
                      onClick={() =>
                        action.mutate({
                          path: "/telegram/test",
                          success: "Тест отправлен",
                        })
                      }
                    >
                      Проверить
                    </Button>
                  </div>
                </>
              )}
            </DataGuard>
          </div>
        </section>
      </div>
    </>
  );
}
