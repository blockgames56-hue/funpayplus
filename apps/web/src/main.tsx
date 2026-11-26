import { createLiveRefresh, matchesLiveQuery, parseLiveEvent } from "./live-updates";
import { LottieMark } from "./lottie-mark";
import { useAppearance, AppearanceMotion } from "./appearance";
import { lazy, Suspense, StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import {
  BrowserRouter,
  Navigate,
  NavLink,
  Route,
  Routes,
  useLocation,
  useNavigate,
} from "react-router-dom";
import {
  QueryClient,
  QueryClientProvider,
  useQueryClient,
} from "@tanstack/react-query";
import { AnimatePresence, motion } from "motion/react";
import { Command } from "cmdk";
import {
  ArrowLeft,
  ArrowUpRight,
  Monitor,
  Cloud,
  ArrowUpToLine,
  ChevronLeft,
  ChevronRight,
  Command as CommandIcon,
  CreditCard,
  Globe2,
  Gift,
  Inbox,
  KeyRound,
  LayoutDashboard,
  Link2,
  LoaderCircle,
  List,
  LogOut,
  Menu,
  Package,
  Plug,
  Search,
  Settings,
  ShieldBan,
  Volume2,
  VolumeX,
  X,
  Zap,
} from "lucide-react";
import { Toaster, toast } from "sonner";
import * as Dialog from "@radix-ui/react-dialog";
import {
  API,
  SERVER_TOKEN_KEY,
  SERVER_URL_KEY,
  accountQuery,
  money,
  request,
  setDesktopToken,
  sound,
  useAction,
  useData,
} from "./api";
import { navigation } from "./navigation";
import { checkServer, serverAddress } from "./server-connection";
import { PageBoundary } from "./page-boundary";
import { useAccountStore, useSettingsStore } from "./store";
import { Avatar, Button, Confirm } from "./ui";
import { eventBus } from "./events";
import type { Account, Dashboard, Lot } from "./types";
import "@fontsource/inter/latin-400.css";
import "@fontsource/inter/cyrillic-400.css";
import "@fontsource/inter/latin-500.css";
import "@fontsource/inter/cyrillic-500.css";
import "@fontsource/inter/latin-600.css";
import "@fontsource/inter/cyrillic-600.css";
import "@fontsource/inter/latin-700.css";
import "@fontsource/inter/cyrillic-700.css";
import "@fontsource/jetbrains-mono/latin-400.css";
import "@fontsource/jetbrains-mono/cyrillic-400.css";
import "./styles.css";

const AccountWorkspace = lazy(() =>
  import("./account-workspace").then((module) => ({
    default: module.AccountWorkspace,
  })),
);
const AccountsPage = lazy(() =>
  import("./account-workspace").then((module) => ({
    default: module.AccountsWorkspace,
  })),
);
const AutoRepliesPage = lazy(() => import("./responder-workspace"));
const BlacklistPage = lazy(() =>
  import("./pages").then((module) => ({ default: module.BlacklistPage })),
);
const DeliveryPage = lazy(() =>
  import("./pages").then((module) => ({ default: module.DeliveryPage })),
);
const FinancialsPage = lazy(() =>
  import("./pages").then((module) => ({ default: module.FinancialsPage })),
);
const HealthPage = lazy(() =>
  import("./pages").then((module) => ({ default: module.HealthPage })),
);
const InboxPage = lazy(() =>
  import("./inbox-workspace").then((module) => ({
    default: module.InboxWorkspace,
  })),
);
const LotsPage = lazy(() =>
  import("./inbox-workspace").then((module) => ({
    default: module.LotsWorkspace,
  })),
);
const Overview = lazy(() =>
  import("./pages").then((module) => ({ default: module.Overview })),
);
const ProxiesPage = lazy(() =>
  import("./pages").then((module) => ({ default: module.ProxiesPage })),
);
const SettingsPage = lazy(() =>
  import("./pages").then((module) => ({ default: module.SettingsPage })),
);
const PluginsPage = lazy(() =>
  import("./plugin-pages").then((module) => ({ default: module.PluginsPage })),
);
const PluginDetailPage = lazy(() =>
  import("./plugin-pages").then((module) => ({
    default: module.PluginDetailPage,
  })),
);
const LoyaltyPage = lazy(() =>
  import("./loyalty").then((module) => ({ default: module.LoyaltyPage })),
);

const client = new QueryClient({
  defaultOptions: { queries: { staleTime: 10000, retry: 1 } },
});
const icons = [
  LayoutDashboard,
  CreditCard,
  Inbox,
  Package,
  KeyRound,
  Globe2,
  ShieldBan,
  CreditCard,
  CommandIcon,
  Gift,
  Plug,
  Settings,
  List,
];

function Login({ onLogin }: { onLogin: () => void }) {
  const [launch, setLaunch] = useState(
    () => localStorage.getItem("funpay-launch") || "",
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const chooseLocal = () => {
    localStorage.removeItem(SERVER_URL_KEY);
    localStorage.removeItem(SERVER_TOKEN_KEY);
    setDesktopToken("");
    localStorage.setItem("funpay-launch", "local");
    window.location.reload();
  };
  const back = () => {
    localStorage.removeItem("funpay-launch");
    setLaunch("");
    setError("");
  };
  return (
    <div className="launch-screen vivid-launch">
      <div className="login-brand">
        <span className="brand-symbol">
          <Zap size={22} />
        </span>
        FUNPAY<span>PLUS</span>
      </div>
      <div className="launch-content">
        <div className="launch-orbit">
          <LottieMark variant="orbit" />
        </div>
        <span className="eyebrow">Ваш магазин. Ваш ритм.</span>
        <h1>
          {launch === "vps" ? (
            "Магазин всегда онлайн."
          ) : launch === "local" ? (
            "Подключаем пространство."
          ) : (
            <>
              Меньше рутины.
              <br />
              <span>Больше движения.</span>
            </>
          )}
        </h1>
        {!launch && (
          <>
            <p>
              Где будет работать приложение? Выберите режим — и можно начинать.
            </p>
            <div className="launch-options">
              <button onClick={chooseLocal}>
                <span className="mode-icon">
                  <Monitor size={26} />
                </span>
                <strong>На этом компьютере</strong>
                <span>Запуск вместе с приложением</span>
                <ArrowUpRight size={20} />
              </button>
              <button
                onClick={() => {
                  localStorage.setItem("funpay-launch", "vps");
                  setLaunch("vps");
                }}
              >
                <span className="mode-icon purple">
                  <Cloud size={26} />
                </span>
                <strong>На своём VPS</strong>
                <span>Автоматизация работает 24/7</span>
                <ArrowUpRight size={20} />
              </button>
            </div>
            <small className="launch-note">
              Без ключей входа. Настройки сохраняются в вашей установке.
            </small>
          </>
        )}
        {launch === "vps" && (
          <>
            <p>
              Введите адрес установленной панели. Подключение произойдёт
              автоматически.
            </p>
            <ServerConnect initialOpen />
            <details className="launch-install">
              <summary>Как установить сервер?</summary>
              <p>Выполните на VPS под root:</p>
              <code>
                curl -fsSL
                https://raw.githubusercontent.com/blockgames56-hue/funpayplus/main/install.sh
                | bash -s -- --repo
                https://github.com/blockgames56-hue/funpayplus.git
              </code>
              <p>
                Без домена откройте SSH-туннель и укажите http://localhost:8080.
              </p>
            </details>
          </>
        )}
        {launch === "local" && (
          <>
            <p>
              Сервер пока недоступен. Проверьте, что он запущен, и повторите
              подключение.
            </p>
            {error && <p role="alert">{error}</p>}
            <Button
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  if (window.__TAURI_INTERNALS__) {
                    const { invoke } = await import("@tauri-apps/api/core");
                    setDesktopToken(await invoke<string>("backend_token"));
                  } else {
                    const session = await request<{ session_token: string }>(
                      "/auth/connect",
                      "POST",
                    );
                    setDesktopToken(session.session_token);
                  }
                  await request("/auth/session");
                  onLogin();
                } catch (reason) {
                  setError((reason as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Повторить подключение
            </Button>
          </>
        )}
        {!!launch && (
          <Button secondary className="login-back" onClick={back}>
            <ArrowLeft size={16} />
            Назад к выбору режима
          </Button>
        )}
      </div>
    </div>
  );
}

function ServerConnect({ initialOpen = false }: { initialOpen?: boolean }) {
  const [open, setOpen] = useState(initialOpen);
  const [url, setUrl] = useState(
    () => localStorage.getItem(SERVER_URL_KEY) || "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const connected = localStorage.getItem(SERVER_URL_KEY);
  // В браузере панель уже открыта с адреса своего сервера; форма нужна
  // только десктоп-приложению, иначе кросс-доменный CORS вводит в заблуждение.

  if (!open)
    return (
      <div className="server-connect">
        <button type="button" onClick={() => setOpen(true)}>
          Подключиться к своему серверу
        </button>
        {connected && (
          <>
            <small>
              Сейчас используется сервер: <code>{connected}</code>
            </small>
            <button
              type="button"
              className="danger"
              onClick={() => {
                localStorage.removeItem(SERVER_URL_KEY);
                localStorage.removeItem(SERVER_TOKEN_KEY);
                localStorage.setItem("funpay-launch", "local");
                window.location.reload();
              }}
            >
              Вернуться к локальному режиму
            </button>
          </>
        )}
      </div>
    );
  async function connect() {
    setBusy(true);
    setError("");
    try {
      const base = serverAddress(url);
      await checkServer(base);
      if (window.__TAURI_INTERNALS__) {
        const { invoke } = await import("@tauri-apps/api/core");
        await invoke("stop_local_backend");
      }
      localStorage.setItem(SERVER_URL_KEY, base);
      localStorage.removeItem(SERVER_TOKEN_KEY);
      localStorage.setItem("funpay-launch", "vps");
      window.location.reload();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div
      className="server-connect expanded"
      onKeyDown={(event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          if (!busy) void connect();
        }
      }}
    >
      <label className="field">
        <span>Адрес сервера</span>
        <input
          value={url}
          onChange={(event) => setUrl(event.target.value)}
          placeholder="https://funpay.example.com или http://127.0.0.1:8080"
          autoComplete="url"
        />
      </label>
      {error && (
        <p role="alert" className="inline-error">
          {error}
        </p>
      )}
      <div className="server-connect-actions">
        <Button secondary type="button" onClick={() => setOpen(false)}>
          Отмена
        </Button>
        <Button
          type="button"
          disabled={busy || !url.trim()}
          onClick={() => void connect()}
        >
          {busy ? (
            <LoaderCircle className="spin" size={15} />
          ) : (
            <Link2 size={15} />
          )}
          Подключиться
        </Button>
      </div>
      <small>
        Сервер разворачивается командой{" "}
        <code>
          powershell -File .\deploy\push-vps.ps1 -Server SERVER -User USER
        </code>
      </small>
    </div>
  );
}

function Terminal({ onLogout }: { onLogout: () => void }) {
  const selected = useAccountStore((s) => s.selected);
  const select = useAccountStore((s) => s.select);
  const ui = useSettingsStore();
  const action = useAction();
  const [palette, setPalette] = useState(false);
  const [mobileNav, setMobileNav] = useState(false);
  const [connected, setConnected] = useState(false);
  const accounts = useData<Account[]>("accounts", "/accounts");
  const dashboard = useData<Dashboard>(
    "dashboard",
    accountQuery("/dashboard", selected),
  );
  const queryClient = useQueryClient();
  const location = useLocation();
  const navigate = useNavigate();
  const currentAccount =
    accounts.data?.find((a) => String(a.id) === selected) ?? accounts.data?.[0];
  useEffect(() => {
    function key(event: KeyboardEvent) {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPalette((value) => !value);
      }
    }
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);
  useEffect(() => {
    if (
      selected &&
      accounts.data &&
      !accounts.data.some((a) => String(a.id) === selected)
    )
      select("");
  }, [selected, accounts.data, select]);
  useEffect(() => {
    let socket: WebSocket | null = null;
    let timer: ReturnType<typeof setTimeout>;
    let disposed = false;
    const refresh = createLiveRefresh((events) => {
      void queryClient.invalidateQueries(
        { predicate: (query) => matchesLiveQuery(query.queryKey, events) },
        { cancelRefetch: false },
      );
    });
    // Tauri uses a one-time socket ticket because WebSocket browsers cannot set Authorization.
    async function connect() {
      try {
        const ticket = await request<{ ticket: string }>("/ws-ticket", "POST");
        if (disposed) return;
        const base = API || window.location.origin;
        socket = new WebSocket(
          `${base.replace(/^http/, "ws")}/api/ws?ticket=${ticket.ticket}`,
        );
        socket.onopen = () => setConnected(true);
        socket.onmessage = (event) => {
          if (disposed) return;
          const data = parseLiveEvent(String(event.data));
          if (!data || data.channel === "ping") return;
          refresh.push(data);
          if (data.data.level === "error") eventBus.emit("ERROR", data.data);
          else if (data.data.event === "ORDER_PAID")
            eventBus.emit("ORDER_PAID", data.data);
          else if (data.data.event === "NEW_MESSAGE")
            eventBus.emit("NEW_MESSAGE", data.data);
          else if (data.channel === "logs")
            eventBus.emit("SYSTEM_LOG", data.data);
        };
        socket.onclose = () => {
          if (disposed) return;
          setConnected(false);
          if (!disposed)
            timer = setTimeout(() => {
              void connect();
            }, 5000);
        };
      } catch {
        if (!disposed)
          timer = setTimeout(() => {
            void connect();
          }, 5000);
      }
    }
    void connect();
    return () => {
      disposed = true;
      refresh.dispose();
      clearTimeout(timer);
      socket?.close();
    };
  }, [queryClient]);
  useEffect(() => {
    const subscriptions = [
      eventBus.on("NEW_MESSAGE", () => sound("message")),
      eventBus.on("ORDER_PAID", () => sound("order")),
      eventBus.on("ERROR", () => sound("error")),
    ];
    return () => subscriptions.forEach((unsubscribe) => unsubscribe());
  }, []);
  async function raiseAll() {
    const rows = await request<Lot[]>(accountQuery("/lots", selected));
    const ids = rows.filter((row) => row.is_active).map((row) => row.id);
    if (!ids.length) {
      toast.info("Сначала синхронизируйте лоты");
      return;
    }
    for (let offset = 0; offset < ids.length; offset += 500)
      await action.mutateAsync({
        path: "/lots/bulk",
        body: { ids: ids.slice(offset, offset + 500), action: "raise" },
        success: "Запросы на поднятие обработаны",
      });
  }
  const current = navigation.find(([path]) =>
    location.pathname.startsWith(path),
  );
  return (
    <div
      className={`app ${ui.compact ? "compact" : ""} ${ui.collapsed ? "collapsed" : ""} ${mobileNav ? "nav-open" : ""}`}
    >
      {mobileNav && (
        <button
          className="nav-backdrop"
          aria-label="Закрыть меню"
          onClick={() => setMobileNav(false)}
        />
      )}
      <aside className="sidebar">
        <LinkLogo />
        <div className="workspace-label">
          <span className="workspace-icon">FP</span>
          <div>
            <strong>Моё пространство</strong>
            <small>FunPay Plus</small>
          </div>
          <span className="tag" title={API || window.location.origin}>
            {localStorage.getItem(SERVER_URL_KEY) ||
            (!window.__TAURI_INTERNALS__ &&
              !["localhost", "127.0.0.1", "[::1]"].includes(
                window.location.hostname,
              ))
              ? "VPS"
              : "LOCAL"}
          </span>
        </div>
        <span className="nav-label">УПРАВЛЕНИЕ</span>
        <nav>
          {[
            "/accounts",
            "/inbox",
            "/lots",
            "/plugins",
            "/auto-replies",
            "/logs",
            "/blacklist",
            "/settings",
          ].map((path) => {
            const label =
              navigation.find(([entry]) => entry === path)?.[1] ?? path;
            const Icon =
              path === "/logs"
                ? List
                : icons[navigation.findIndex(([entry]) => entry === path)];
            return (
              <NavLink
                key={path}
                to={path}
                title={ui.collapsed ? label : undefined}
                onClick={() => setMobileNav(false)}
                className={({ isActive }) =>
                  `nav-item ${isActive ? "active" : ""}`
                }
              >
                <Icon size={18} />
                <span>{label}</span>
                {path === "/accounts" && !!accounts.data?.length && (
                  <b>{accounts.data.length}</b>
                )}
              </NavLink>
            );
          })}
        </nav>
        <div className="sidebar-bottom">
          <div className="sidebar-status">
            <i className={connected ? "online-dot" : "offline-dot"} />
            <span>{connected ? "Терминал подключён" : "Переподключение…"}</span>
          </div>
          <div className="sidebar-controls">
            <button
              className="icon-button"
              title={ui.muted ? "Включить звуки" : "Выключить звуки"}
              onClick={() => ui.toggle("muted")}
            >
              {ui.muted ? <VolumeX size={17} /> : <Volume2 size={17} />}
            </button>
            <button
              className="icon-button"
              title="Компактный интерфейс"
              onClick={() => ui.toggle("compact")}
            >
              <Menu size={17} />
            </button>
            <button
              className="icon-button collapse-control"
              title="Свернуть меню"
              onClick={() => ui.toggle("collapsed")}
            >
              {ui.collapsed ? (
                <ChevronRight size={17} />
              ) : (
                <ChevronLeft size={17} />
              )}
            </button>
            <button
              className="icon-button"
              title="Выйти"
              onClick={() => {
                void request("/auth/logout", "POST").then(onLogout);
              }}
            >
              <LogOut size={16} />
            </button>
          </div>
          <div className="version">
            <span>FUNPAY PLUS</span>
            <span>v0.1.7</span>
          </div>
        </div>
      </aside>
      <div className="main-area">
        <header className="topbar">
          <button
            className="icon-button mobile-menu"
            title="Открыть меню"
            onClick={() => setMobileNav(true)}
          >
            <Menu size={20} />
          </button>
          <div className="account-selector">
            <span className="account-selector-icon">
              <CreditCard size={16} />
            </span>
            <select
              aria-label="Фильтр аккаунтов"
              value={selected}
              onChange={(event) => select(event.target.value)}
            >
              <option value="">Все аккаунты</option>
              {accounts.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.username}
                </option>
              ))}
            </select>
          </div>
          <div className="breadcrumb">
            Рабочее пространство <span>/</span> <b>{current?.[1] || "Обзор"}</b>
          </div>
          <div className="topbar-right">
            {currentAccount && (
              <div className="header-account">
                <Avatar
                  name={currentAccount.username}
                  url={currentAccount.avatar_url}
                  path={`/avatars/account/${currentAccount.id}`}
                  small
                />
                <strong>{currentAccount.username}</strong>
              </div>
            )}
            <button className="command-button" onClick={() => setPalette(true)}>
              <Search size={14} />
              <span>Быстрый переход</span>
              <kbd>Ctrl K</kbd>
            </button>
            <div className="header-balance">
              <small>Общий баланс</small>
              <strong>{money(dashboard.data?.balances.RUB || 0)}</strong>
            </div>
            <Confirm
              trigger={
                <Button secondary disabled={action.isPending}>
                  <ArrowUpToLine size={15} />
                  <span>Поднять лоты</span>
                </Button>
              }
              title="Поднять активные лоты?"
              text="Запросы будут отправлены в FunPay для аккаунтов в текущем фильтре."
              onConfirm={raiseAll}
            />
          </div>
        </header>
        <main>
          <AnimatePresence mode="wait">
            <motion.div
              key={location.pathname}
              initial={{ opacity: 0, y: 12 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.26, ease: [0.22, 1, 0.36, 1] }}
            >
              <PageBoundary key={location.pathname}>
                <Suspense
                  fallback={
                    <div className="route-loading">
                      <LoaderCircle className="spin" size={24} />
                      <span>Открываем раздел…</span>
                    </div>
                  }
                >
                  <Routes>
                    <Route path="/dashboard" element={<Overview />} />
                    <Route path="/accounts" element={<AccountsPage />} />
                    <Route
                      path="/accounts/:accountId"
                      element={<AccountWorkspace />}
                    />
                    <Route path="/inbox" element={<InboxPage />} />
                    <Route path="/lots" element={<LotsPage />} />
                    <Route path="/auto-delivery" element={<DeliveryPage />} />
                    <Route path="/proxies" element={<ProxiesPage />} />
                    <Route path="/blacklist" element={<BlacklistPage />} />
                    <Route path="/financials" element={<FinancialsPage />} />
                    <Route path="/auto-replies" element={<AutoRepliesPage />} />
                    <Route path="/loyalty" element={<LoyaltyPage />} />
                    <Route path="/plugins" element={<PluginsPage />} />
                    <Route
                      path="/plugins/:pluginId"
                      element={<PluginDetailPage />}
                    />
                    <Route path="/logs" element={<HealthPage />} />
                    <Route path="/settings/*" element={<SettingsPage />} />
                    <Route
                      path="*"
                      element={<Navigate replace to="/dashboard" />}
                    />
                  </Routes>
                </Suspense>
              </PageBoundary>
            </motion.div>
          </AnimatePresence>
        </main>
        <footer className="app-footer">
          <span>
            <i className={connected ? "online-dot" : "offline-dot"} />
            {connected ? "Синхронизация подключена" : "Ожидание соединения"}
          </span>
          <span>Ваше рабочее пространство</span>
        </footer>
      </div>
      <Dialog.Root open={palette} onOpenChange={setPalette}>
        <Dialog.Portal>
          <Dialog.Overlay className="modal-overlay" />
          <Dialog.Content className="command-modal">
            <Dialog.Title className="sr-only">Переход к разделу</Dialog.Title>
            <Dialog.Description className="sr-only">
              Найдите раздел по названию
            </Dialog.Description>
            <Command label="Быстрый переход">
              <div className="command-input">
                <Search size={18} />
                <Command.Input placeholder="Куда перейдём?" />
                <Dialog.Close className="icon-button" aria-label="Закрыть">
                  <X size={16} />
                </Dialog.Close>
              </div>
              <Command.List>
                <Command.Empty>Ничего не найдено</Command.Empty>
                <Command.Group heading="РАБОЧЕЕ ПРОСТРАНСТВО">
                  {navigation.map(([path, label], index) => {
                    const Icon = icons[index];
                    return (
                      <Command.Item
                        value={label}
                        key={path}
                        onSelect={() => {
                          navigate(path);
                          setPalette(false);
                        }}
                      >
                        <Icon size={17} />
                        {label}
                        <ChevronRight size={14} />
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              </Command.List>
              <div className="command-footer">
                <CommandIcon size={13} /> ↑ ↓ для выбора{" "}
                <span>Enter для перехода</span>
              </div>
            </Command>
          </Dialog.Content>
        </Dialog.Portal>
      </Dialog.Root>
    </div>
  );
}
function LinkLogo() {
  return (
    <NavLink to="/dashboard" className="brand">
      <span className="brand-symbol">
        <Zap size={22} />
      </span>
      <strong>
        FUNPAY <em>PLUS</em>
      </strong>
      <i className="online-dot" />
    </NavLink>
  );
}
function App() {
  const [authenticated, setAuthenticated] = useState<boolean | null>(null);
  useEffect(() => {
    let live = true;
    async function init() {
      const desktop = Boolean(window.__TAURI_INTERNALS__);
      const mode = localStorage.getItem("funpay-launch");
      localStorage.removeItem(SERVER_TOKEN_KEY);
      if (!mode || (mode === "vps" && !localStorage.getItem(SERVER_URL_KEY))) {
        if (live) setAuthenticated(false);
        return;
      }
      if (!localStorage.getItem(SERVER_URL_KEY) && desktop) {
        const { invoke } = await import("@tauri-apps/api/core");
        setDesktopToken(await invoke<string>("backend_token"));
      }
      // Ожидание старта нужно только локальному sidecar; удалённый сервер
      // уже работает — долгие ретраи не нужны.
      const remote = Boolean(localStorage.getItem(SERVER_URL_KEY));
      for (
        let attempt = 0;
        attempt < (desktop && !remote ? 30 : 1);
        attempt++
      ) {
        try {
          if (remote) setDesktopToken(await checkServer(API));
          else if (!desktop) {
            const session = await request<{ session_token: string }>(
              "/auth/connect",
              "POST",
            );
            setDesktopToken(session.session_token);
          }
          await request("/auth/session");
          if (live) setAuthenticated(true);
          return;
        } catch {
          if (desktop && !remote && attempt < 29)
            await new Promise((resolve) => setTimeout(resolve, 500));
        }
      }
      if (live) setAuthenticated(false);
    }
    void init().catch(() => setAuthenticated(false));
    function expired() {
      setAuthenticated(null);
      client.clear();
      void init().catch(() => setAuthenticated(false));
    }
    window.addEventListener("session-expired", expired);
    const shutdownError = () =>
      toast.error(
        "Не удалось скрыть все лоты. Выход отменён; проверьте журнал и повторите попытку.",
      );
    window.addEventListener("shutdown-error", shutdownError);
    const backendExited = () => {
      if (!localStorage.getItem(SERVER_URL_KEY)) {
        setAuthenticated(false);
        client.clear();
        toast.error(
          "Локальный сервер остановился. Проверьте, не занят ли порт 8765, и перезапустите приложение.",
        );
      }
    };
    window.addEventListener("local-backend-exited", backendExited);
    return () => {
      live = false;
      window.removeEventListener("session-expired", expired);
      window.removeEventListener("shutdown-error", shutdownError);
      window.removeEventListener("local-backend-exited", backendExited);
    };
  }, []);
  return (
    <>
      <Toaster
        theme={
          useAppearance((s) => s.theme).startsWith("light") ? "light" : "dark"
        }
        position="bottom-right"
        richColors
        closeButton
      />
      {authenticated === null ? (
        <div className="boot">
          <LottieMark variant="orbit" />
          <p>Собираем ваше рабочее пространство…</p>
        </div>
      ) : authenticated ? (
        <Terminal
          onLogout={() => {
            localStorage.removeItem("funpay-launch");
            setDesktopToken("");
            if (localStorage.getItem(SERVER_URL_KEY)) {
              localStorage.removeItem(SERVER_TOKEN_KEY);
              setDesktopToken("");
            }
            setAuthenticated(false);
            client.clear();
          }}
        />
      ) : (
        <Login onLogin={() => setAuthenticated(true)} />
      )}
    </>
  );
}
createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <BrowserRouter>
        <AppearanceMotion>
          <App />
        </AppearanceMotion>
      </BrowserRouter>
    </QueryClientProvider>
  </StrictMode>,
);
import "./appearance.css";

import "./motion-design.css";

import "./polish.css";
