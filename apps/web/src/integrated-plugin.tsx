import { useSyncedSettings } from "./synced-settings";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { requestId } from "./request-id";
import { PluginPart, sectionInfo, usePluginSection } from "./plugin-sections";
import { useState } from "react";
import {
  Check,
  CircleHelp,
  Clock3,
  Eye,
  EyeOff,
  FileJson,
  HeartHandshake,
  KeyRound,
  Link2,
  LoaderCircle,
  PackagePlus,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  ShieldCheck,
  Snowflake,
  Sparkles,
  Trash2,
  Wallet,
  X,
} from "lucide-react";
import { toast } from "sonner";
import { date, request, useAction, useData } from "./api";
import { Button, Confirm, Field, Modal, Pending } from "./ui";
import type { Account } from "./types";

type Kind =
  | "auto-smm"
  | "steam-points"
  | "steam-rent"
  | "auto-stars"
  | "support-tickets";
type Provider = {
  id: string;
  name: string;
  url: string;
  api_key: string;
  has_api_key?: boolean;
};
type Settings = {
  enabled: boolean;
  confirm_target?: boolean;
  start_message?: string;
  success_message?: string;
  providers?: Provider[];
  allowed_domains?: string[];
  api_key?: string;
  has_api_key?: boolean;
  check_interval?: number;
  low_balance_threshold?: string | number;
  hide_on_low_balance?: boolean;
  fragment_cookie?: string;
  fragment_hash?: string;
  ton_api_key?: string;
  mnemonic?: string;
  has_fragment_cookie?: boolean;
  has_fragment_hash?: boolean;
  has_ton_api_key?: boolean;
  has_mnemonic?: boolean;
  wallet_address?: string;
  max_ton_per_order?: string | number;
  min_stars?: number;
  max_stars?: number;
  show_sender?: boolean;
  warn_minutes?: number;
  review_bonus_minutes?: number;
  auto_hide_empty?: boolean;
  require_photo_review?: boolean;
  account_id?: number | null;
  phpsessid?: string;
  has_phpsessid?: boolean;
  min_age_hours?: number;
  interval_minutes?: number;
  batch_limit?: number;
  message?: string;
  only_delivered?: boolean;
};
type Binding = {
  id: number;
  lot_id: number;
  title: string;
  funpay_item_id: string;
  quantity: number;
  provider_id: string;
  service_id: string;
  is_active: boolean;
  is_extension: boolean;
};
type ResolvedLot = {
  lot_id: number;
  funpay_lot_id: string;
  title: string;
  price: string;
  category_title: string;
  amount: number | null;
  is_active: boolean;
  account_id: number;
  account: string;
};
type Job = {
  id: number;
  state: string;
  quantity: number;
  target: string | null;
  order_number: string;
  buyer: string;
  last_error: string | null;
  remote_id: string | null;
  review_image?: string;
  provider_status?: string;
  remains?: number;
  created_at: string;
};
type Inventory = {
  id: number;
  binding_id: number;
  label: string;
  state: string;
  last_error: string | null;
};
type Lease = {
  id: number;
  label: string;
  state: string;
  expires_at: string;
  buyer: string;
  order_number: string;
};
type Support = {
  id: number;
  state: string;
  remote_id: string | null;
  orders: string[];
  last_error: string | null;
  created_at: string;
};
type Candidate = {
  id: number;
  funpay_order_id: string;
  buyer_name: string;
  description: string;
  created_at: string;
};
type Data = {
  settings: Settings;
  configured: boolean;
  bindings: Binding[];
  jobs: Job[];
  inventory?: Inventory[];
  leases?: Lease[];
  requests?: Support[];
  candidates?: Candidate[];
};
type Balance = { name: string; balance: string; currency: string };
type Service = {
  service: number | string;
  name: string;
  rate: string;
  min: number;
  max: number;
  category: string;
};
const meta = {
  "auto-smm": {
    label: "AutoSMM",
    tagline: "От оплаты до выполнения у вашего поставщика",
    color: "violet",
    unit: "Количество услуги за 1 шт.",
    version: "AutoSMM 4.2",
    help: "Выберите поставщика, привяжите услугу к своему лоту и включите плагин. Покупатель отправит ссылку и подтвердит её знаком +.",
  },
  "steam-points": {
    label: "Steam Points",
    tagline: "Очки Steam без ручного переноса заказов",
    color: "blue",
    unit: "Очков за 1 шт.",
    version: "Steam Points 3.0",
    help: "Укажите API ключ BuySteamPoints и количество очков для каждого лота. Ссылку на профиль плагин запросит у покупателя.",
  },
  "steam-rent": {
    label: "Auto Steam Rent",
    tagline: "Ваш пул аккаунтов и все аренды на одном экране",
    color: "teal",
    unit: "Часов за 1 шт.",
    version: "Auto Rent Steam 0.3.2",
    help: "Привяжите лот, добавьте свои Steam-аккаунты с maFile и проверьте вход. По окончании аренды аккаунт вернётся в пул только после подтверждённой смены пароля.",
  },
  "auto-stars": {
    label: "Auto Stars",
    tagline: "Telegram Stars через Fragment и ваш TON-кошелёк",
    color: "amber",
    unit: "Stars за 1 шт.",
    version: "Auto Stars 2.0 · новый адаптер",
    help: "Подключите сессию Fragment, TON API и свой кошелёк. Установите лимит TON на заказ. Получатель подтвердит свой username перед оплатой.",
  },
  "support-tickets": {
    label: "AutoTicket · Поддержка",
    tagline: "Обращения по заказам, ожидающим подтверждения",
    color: "rose",
    unit: "",
    version: "AutoTicket 1.3.0",
    help: "Выберите аккаунт FunPay. Автоматические обращения создаются только по достоверно датированным заказам старше заданного срока. Каждый заказ попадает в обращение один раз.",
  },
};
const states: Record<string, string> = {
  waiting_target: "Ожидает получателя",
  confirm_target: "Ожидает подтверждения",
  waiting_review: "Проверка фото",
  waiting_stock: "Ожидает аккаунт",
  verifying: "Проверка входа",
  ready: "В очереди",
  preparing: "Подготовка",
  submitting: "Отправка",
  submitted: "У поставщика",
  completed: "Выполнен",
  message_sending: "Отправка покупателю",
  uncertain: "Проверьте результат",
  attention: "Требует внимания",
  error: "Ошибка",
  cancelled: "Отменён",
  free: "Свободен",
  leased: "В аренде",
  frozen: "Заморожен",
  rotating: "Смена пароля",
  quarantine: "Нужна проверка",
  active: "Активна",
  finished: "Завершена",
  sent: "Отправлено",
  superseded: "Заменено новым",
};

function Secret({
  label,
  value,
  saved,
  onChange,
  multiline = false,
}: {
  label: string;
  value: string;
  saved?: boolean;
  onChange: (value: string) => void;
  multiline?: boolean;
}) {
  const [visible, setVisible] = useState(false);
  return (
    <Field
      label={label}
      hint={
        saved
          ? "Сохранено. Оставьте поле пустым, чтобы сохранить текущее значение."
          : undefined
      }
    >
      <div className="adapted-secret">
        {multiline && visible ? (
          <textarea
            aria-label={label}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            autoComplete="off"
          />
        ) : (
          <input
            aria-label={label}
            type={visible ? "text" : "password"}
            value={value}
            placeholder={saved ? "•••••••• Сохранено" : ""}
            onChange={(event) => onChange(event.target.value)}
            autoComplete="off"
          />
        )}
        <button
          type="button"
          title={visible ? "Скрыть" : "Показать"}
          onClick={() => setVisible(!visible)}
        >
          {visible ? <EyeOff size={16} /> : <Eye size={16} />}
        </button>
      </div>
    </Field>
  );
}
function Choice({
  title,
  text,
  checked,
  onChange,
}: {
  title: string;
  text: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <button
      type="button"
      className={`adapted-choice ${checked ? "selected" : ""}`}
      onClick={() => onChange(!checked)}
      aria-pressed={checked}
    >
      <div>
        <strong>{title}</strong>
        <span>{text}</span>
      </div>
      <i className={`mini-switch ${checked ? "on" : ""}`} />
    </button>
  );
}
const emptyAccount = {
  binding_id: 0,
  label: "",
  login: "",
  password: "",
  shared_secret: "",
  identity_secret: "",
  device_id: "",
  steamid: "",
};

export function IntegratedPlugin({ kind }: { kind: Kind }) {
  const base = `/plugins/integrated/${kind}`;
  const info = meta[kind];
  const query = useData<Data>(`integrated-${kind}`, base);
  const accounts = useData<Account[]>("integrated-accounts", "/accounts");
  const action = useAction();
  const [form, setForm] = useState<Settings | null>(null);
  const managed = usePluginSection();
  const [localTab, setTab] = useState("setup");
  const tab = managed || localTab;
  const [balances, setBalances] = useState<Balance[]>([]);
  const [testing, setTesting] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [bindingOpen, setBindingOpen] = useState(false);
  const [editingBinding, setEditingBinding] = useState<number | null>(null);
  const [binding, setBinding] = useState({
    lot_id: 0,
    quantity: kind === "auto-stars" ? 50 : kind === "steam-points" ? 1000 : 1,
    provider_id: "",
    service_id: "",
    is_extension: false,
    is_active: true,
  });
  const [lotQuery, setLotQuery] = useState("");
  const [lotResolved, setLotResolved] = useState<ResolvedLot | null>(null);
  const [lotBusy, setLotBusy] = useState(false);
  const [lotError, setLotError] = useState("");
  const [services, setServices] = useState<Service[]>([]);
  const [serviceSearch, setServiceSearch] = useState("");
  const [inventoryOpen, setInventoryOpen] = useState(false);
  const [editingAccount, setEditingAccount] = useState<number | null>(null);
  const [inventoryForm, setInventoryForm] = useState(emptyAccount);
  const [jobSearch, setJobSearch] = useState("");
  const [jobFilter, setJobFilter] = useState("all");
  const [targets, setTargets] = useState<Record<number, string>>({});
  const [selectedOrders, setSelectedOrders] = useState<number[]>([]);
  useSyncedSettings<Settings | null>(query.data?.settings, setForm);
  function update<K extends keyof Settings>(key: K, value: Settings[K]) {
    setForm((old) => (old ? { ...old, [key]: value } : old));
    setDirty(true);
  }
  const auto = useAutoSave({ value: form, dirty, enabled: !!form,
    save: async value => {
      if (!value) return;
      const clean = Object.fromEntries(Object.entries(value).filter(([key]) => !key.startsWith("has_")));
      if (value.allowed_domains) clean.allowed_domains = value.allowed_domains.map(v => v.trim()).filter(Boolean);
      if (value.providers) clean.providers = value.providers.map(({ has_api_key: _saved, ...provider }) => provider);
      await request(`${base}/settings`, "PUT", clean);
    }, onSaved: () => setDirty(false), queryKeys: ["plugin-catalog"] });
  async function test() {
    setTesting(true);
    try {
      const data = await request<{ balances?: Balance[]; message?: string }>(
        `${base}/test`,
        "POST",
      );
      setBalances(data.balances || []);
      toast.success(data.message || "Подключение проверено");
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setTesting(false);
    }
  }
  async function loadServices() {
    try {
      const data = await request<Service[]>(
        `/plugins/integrated/auto-smm/services/${binding.provider_id}`,
      );
      setServices(data);
      toast.success(`Загружено услуг: ${data.length}`);
    } catch (error) {
      toast.error((error as Error).message);
    }
  }
  async function saveBinding() {
    try {
      await action.mutateAsync({
        path: `${base}/bindings${editingBinding ? `/${editingBinding}` : ""}`,
        method: editingBinding ? "PUT" : "POST",
        body: binding,
        success: editingBinding ? "Привязка обновлена" : "Лот привязан",
      });
      setBindingOpen(false);
    } catch {
      /* useAction displays errors */
    }
  }
  async function resolveLot(raw?: string) {
    const value = (raw ?? lotQuery).trim();
    if (!value) return;
    setLotBusy(true);
    setLotError("");
    try {
      const data = await request<ResolvedLot>(`${base}/lots/resolve`, "POST", {
        query: value,
      });
      setLotResolved(data);
      setLotQuery(String(data.funpay_lot_id));
      setBinding((old) => ({ ...old, lot_id: data.lot_id }));
    } catch (error) {
      setLotResolved(null);
      setLotError((error as Error).message);
    } finally {
      setLotBusy(false);
    }
  }
  async function saveInventory() {
    try {
      await action.mutateAsync({
        path: `/plugins/integrated/steam-rent/inventory${editingAccount ? `/${editingAccount}` : ""}`,
        method: editingAccount ? "PUT" : "POST",
        body: inventoryForm,
        success: editingAccount
          ? "Данные проверены и обновлены"
          : "Аккаунт добавлен",
      });
      setInventoryOpen(false);
      setInventoryForm(emptyAccount);
    } catch {
      /* useAction displays errors */
    }
  }
  async function readMaFile(file?: File) {
    if (!file) return;
    try {
      if (file.size > 100000) throw new Error("Слишком большой maFile");
      const data = JSON.parse(await file.text());
      setInventoryForm((old) => ({
        ...old,
        login: old.login || data.account_name || "",
        shared_secret: data.shared_secret || "",
        identity_secret: data.identity_secret || "",
        device_id: data.device_id || "",
        steamid: String(
          data.Session?.SteamID ||
            data.Session?.SteamId ||
            data.session?.steamid ||
            data.steamid ||
            "",
        ),
      }));
      toast.success("Поля maFile заполнены");
    } catch {
      toast.error("Не удалось прочитать maFile");
    }
  }
  async function editInventory(id: number) {
    try {
      const values = await request<typeof emptyAccount>(
        `/plugins/integrated/steam-rent/inventory/${id}/credentials`,
      );
      setInventoryForm(values);
      setEditingAccount(id);
      setInventoryOpen(true);
    } catch (error) {
      toast.error((error as Error).message);
    }
  }
  const operate = (id: number, operation: string) =>
    action.mutate({
      path: `${base}/jobs/${id}/${operation}`,
      success: "Операция принята",
    });
  const tabs =
    kind === "support-tickets"
      ? [
          ["setup", "Подключение"],
          ["requests", "Заказы и обращения"],
        ]
      : [
          ["setup", "Подключение"],
          ["bindings", "Мои лоты"],
          ...(kind === "steam-rent"
            ? [
                ["inventory", "Аккаунты"],
                ["leases", "Аренды"],
              ]
            : []),
          ["jobs", "История заказов"],
        ];
  const jobs = query.data?.jobs || [];
  const waiting = jobs.filter((job) =>
    [
      "waiting_target",
      "confirm_target",
      "waiting_review",
      "waiting_stock",
      "ready",
      "preparing",
      "submitting",
      "submitted",
    ].includes(job.state),
  ).length;
  const visibleJobs = jobs.filter(
    (job) =>
      `${job.order_number} ${job.buyer} ${job.target || ""}`
        .toLowerCase()
        .includes(jobSearch.toLowerCase()) &&
      (jobFilter === "all" ||
        (jobFilter === "done"
          ? job.state === "completed"
          : jobFilter === "attention"
            ? ["attention", "uncertain", "error"].includes(job.state)
            : ![
                "completed",
                "cancelled",
                "attention",
                "uncertain",
                "error",
              ].includes(job.state))),
  );
  const attention = jobs.filter((job) =>
    ["attention", "error", "uncertain"].includes(job.state),
  ).length;
  if (query.isLoading) return <Pending />;
  if (query.isError)
    return (
      <div className="panel adapted-error">
        Не удалось загрузить плагин.{" "}
        <Button onClick={() => void query.refetch()}>Повторить</Button>
      </div>
    );
  if (!form) return null;
  return (
    <div className={`adapted-plugin theme-${info.color}`}>
      <div className="adapted-intro" hidden={!!managed}>
        <div className="adapted-intro-icon">
          <Sparkles size={26} />
        </div>
        <div>
          <span>{info.version}</span>
          <h2>{info.tagline}</h2>
          <p>{info.help}</p>
        </div>
        <span
          className={`adapted-ready ${query.data?.configured ? "ready" : ""}`}
        >
          {query.data?.configured ? (
            <Check size={14} />
          ) : (
            <CircleHelp size={14} />
          )}
          {query.data?.configured ? "Готов к работе" : "Нужна настройка"}
        </span>
      </div>
      <div className="adapted-stats" hidden={!!managed}>
        <div>
          <span>Привязано лотов</span>
          <strong>{query.data?.bindings.length || 0}</strong>
        </div>
        <div>
          <span>В работе</span>
          <strong>{waiting}</strong>
        </div>
        <div className={attention ? "attention" : ""}>
          <span>Требуют внимания</span>
          <strong>{attention}</strong>
        </div>
        {kind === "steam-rent" && (
          <div>
            <span>Свободно аккаунтов</span>
            <strong>
              {query.data?.inventory?.filter((row) => row.state === "free")
                .length || 0}
            </strong>
          </div>
        )}
      </div>
      <nav
        className="adapted-tabs"
        aria-label="Разделы настроек плагина"
        hidden={!!managed}
      >
        {tabs.map(([id, title]) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            aria-current={tab === id ? "page" : undefined}
          >
            {title}
            {id === "jobs" && attention > 0 && <i>{attention}</i>}
          </button>
        ))}
      </nav>
      {["setup", "connection", "wallet", "settings", "messages"].includes(
        tab,
      ) && (
        <div className="adapted-setup">
          <section className="panel adapted-form">
            <div className="adapted-section-title">
              <KeyRound size={18} />
              <div>
                <h3>{managed ? sectionInfo[managed].label : "Подключение"}</h3>
                <p>
                  {managed && !["connection", "wallet"].includes(managed)
                    ? sectionInfo[managed].description
                    : "Ключи и пароли сохраняются в зашифрованном виде."}
                </p>
              </div>
            </div>
            {kind === "auto-smm" && (
              <>
                <PluginPart section="connection">
                  <div className="adapted-providers">
                    {form.providers?.map((provider, index) => (
                      <div className="adapted-provider" key={provider.id}>
                        <div className="adapted-provider-title">
                          <strong>Поставщик {index + 1}</strong>
                          <button
                            className="icon-button danger"
                            aria-label="Удалить поставщика"
                            onClick={() =>
                              update(
                                "providers",
                                form.providers?.filter(
                                  (row) => row.id !== provider.id,
                                ),
                              )
                            }
                          >
                            <X size={15} />
                          </button>
                        </div>
                        <div className="two-fields">
                          <Field label="Название">
                            <input
                              value={provider.name}
                              onChange={(event) =>
                                update(
                                  "providers",
                                  form.providers?.map((row) =>
                                    row.id === provider.id
                                      ? { ...row, name: event.target.value }
                                      : row,
                                  ),
                                )
                              }
                            />
                          </Field>
                          <Field label="Адрес API">
                            <input
                              type="url"
                              placeholder="https://provider.example/api/v2"
                              value={provider.url}
                              onChange={(event) =>
                                update(
                                  "providers",
                                  form.providers?.map((row) =>
                                    row.id === provider.id
                                      ? { ...row, url: event.target.value }
                                      : row,
                                  ),
                                )
                              }
                            />
                          </Field>
                        </div>
                        <Secret
                          label="API ключ поставщика"
                          value={provider.api_key}
                          saved={provider.has_api_key}
                          onChange={(value) =>
                            update(
                              "providers",
                              form.providers?.map((row) =>
                                row.id === provider.id
                                  ? { ...row, api_key: value }
                                  : row,
                              ),
                            )
                          }
                        />
                      </div>
                    ))}
                  </div>
                  <Button
                    secondary
                    onClick={() =>
                      update("providers", [
                        ...(form.providers || []),
                        {
                          id: requestId(),
                          name: "",
                          url: "",
                          api_key: "",
                        },
                      ])
                    }
                    disabled={(form.providers?.length || 0) >= 10}
                  >
                    <Plus size={15} /> Добавить поставщика
                  </Button>
                </PluginPart>
                <PluginPart section="settings">
                  <Field
                    label="Разрешённые домены получателей"
                    hint="По одному домену в строке. Например: t.me, vk.com."
                  >
                    <textarea
                      value={form.allowed_domains?.join("\n") || ""}
                      onChange={(event) =>
                        update(
                          "allowed_domains",
                          event.target.value.split("\n"),
                        )
                      }
                    />
                  </Field>
                  <Field label="Проверять статус каждые, секунд">
                    <input
                      type="number"
                      min={60}
                      max={3600}
                      value={form.check_interval || 300}
                      onChange={(event) =>
                        update("check_interval", Number(event.target.value))
                      }
                    />
                  </Field>
                </PluginPart>
              </>
            )}
            {kind === "steam-points" && (
              <PluginPart section="connection">
                <div className="adapted-provider-note">
                  <ShieldCheck size={19} />
                  <div>
                    <strong>BuySteamPoints</strong>
                    <span>
                      api.buysteampoints.com · API сервиса из исходного плагина
                    </span>
                  </div>
                </div>
                <Secret
                  label="API ключ BuySteamPoints"
                  value={form.api_key || ""}
                  saved={form.has_api_key}
                  onChange={(value) => update("api_key", value)}
                />
              </PluginPart>
            )}
            {kind === "auto-stars" && (
              <>
                <PluginPart section="connection">
                  <Secret
                    label="Cookie Fragment"
                    value={form.fragment_cookie || ""}
                    saved={form.has_fragment_cookie}
                    onChange={(value) => update("fragment_cookie", value)}
                  />
                  <Secret
                    label="Hash сессии Fragment"
                    value={form.fragment_hash || ""}
                    saved={form.has_fragment_hash}
                    onChange={(value) => update("fragment_hash", value)}
                  />
                </PluginPart>
                <PluginPart section="wallet">
                  <Secret
                    label="TON API ключ"
                    value={form.ton_api_key || ""}
                    saved={form.has_ton_api_key}
                    onChange={(value) => update("ton_api_key", value)}
                  />
                  <Secret
                    label="Seed-фраза TON (24 слова)"
                    multiline
                    value={form.mnemonic || ""}
                    saved={form.has_mnemonic}
                    onChange={(value) => update("mnemonic", value)}
                  />
                  <Field
                    label="Адрес вашего кошелька V4R2"
                    hint="Адрес будет проверен на соответствие seed-фразе."
                  >
                    <input
                      value={form.wallet_address || ""}
                      placeholder="UQ… или EQ…"
                      onChange={(event) =>
                        update("wallet_address", event.target.value)
                      }
                    />
                  </Field>
                </PluginPart>
                <PluginPart section="settings">
                  <div className="two-fields">
                    <Field label="Лимит TON на один заказ">
                      <input
                        type="number"
                        min={0.01}
                        step={0.01}
                        value={form.max_ton_per_order || ""}
                        onChange={(event) =>
                          update("max_ton_per_order", event.target.value)
                        }
                      />
                    </Field>
                    <Field label="Минимум Stars">
                      <input
                        type="number"
                        min={1}
                        value={form.min_stars || 50}
                        onChange={(event) =>
                          update("min_stars", Number(event.target.value))
                        }
                      />
                    </Field>
                    <Field label="Максимум Stars">
                      <input
                        type="number"
                        min={1}
                        value={form.max_stars || 100000}
                        onChange={(event) =>
                          update("max_stars", Number(event.target.value))
                        }
                      />
                    </Field>
                  </div>
                  <Choice
                    title="Показывать отправителя"
                    text="Получатель увидит отправителя Stars."
                    checked={!!form.show_sender}
                    onChange={(value) => update("show_sender", value)}
                  />
                </PluginPart>
              </>
            )}
            {kind === "steam-rent" && (
              <PluginPart section="settings">
                <div className="adapted-provider-note">
                  <ShieldCheck size={20} />
                  <div>
                    <strong>Проверенная смена пароля</strong>
                    <span>
                      После аренды вход с новым паролем проверяется через Steam.
                      Если проверка не прошла, аккаунт не выдаётся повторно.
                    </span>
                  </div>
                </div>
                <div className="two-fields">
                  <Field label="Предупредить за, минут">
                    <input
                      type="number"
                      min={0}
                      max={120}
                      value={form.warn_minutes ?? 10}
                      onChange={(event) =>
                        update("warn_minutes", Number(event.target.value))
                      }
                    />
                  </Field>
                  <Field label="Бонус за отзыв 5★, минут">
                    <input
                      type="number"
                      min={0}
                      max={1440}
                      value={form.review_bonus_minutes || 0}
                      onChange={(event) =>
                        update(
                          "review_bonus_minutes",
                          Number(event.target.value),
                        )
                      }
                    />
                  </Field>
                </div>
                <Choice
                  title="Скрывать лоты без свободных аккаунтов"
                  text="Плагин вернёт лот в продажу, когда аккаунт снова будет доступен."
                  checked={!!form.auto_hide_empty}
                  onChange={(value) => update("auto_hide_empty", value)}
                />
                <Choice
                  title="Фотопроверка продавцом"
                  text="До выдачи покупатель отправит фото. Вы сможете принять или отклонить его в истории заказов."
                  checked={!!form.require_photo_review}
                  onChange={(value) => update("require_photo_review", value)}
                />
                <div className="adapted-command-chips">
                  <span>!код</span>
                  <span>!время</span>
                  <span>!аренда</span>
                  <span>!продлить</span>
                </div>
              </PluginPart>
            )}
            {kind === "support-tickets" && (
              <>
                <PluginPart section="connection">
                  <Field label="Аккаунт FunPay">
                    <select
                      value={form.account_id || 0}
                      onChange={(event) =>
                        update("account_id", Number(event.target.value) || null)
                      }
                    >
                      <option value={0}>Выберите аккаунт</option>
                      {accounts.data?.map((account) => (
                        <option key={account.id} value={account.id}>
                          {account.username}
                        </option>
                      ))}
                    </select>
                  </Field>
                  <Secret
                    label="PHPSESSID поддержки (если SSO недоступен)"
                    value={form.phpsessid || ""}
                    saved={form.has_phpsessid}
                    onChange={(value) => update("phpsessid", value)}
                  />
                </PluginPart>
                <PluginPart section="settings">
                  <div className="two-fields">
                    <Field label="Заказы старше, часов">
                      <input
                        type="number"
                        min={24}
                        value={form.min_age_hours || 24}
                        onChange={(event) =>
                          update("min_age_hours", Number(event.target.value))
                        }
                      />
                    </Field>
                    <Field label="Интервал обращений, минут">
                      <input
                        type="number"
                        min={60}
                        value={form.interval_minutes || 60}
                        onChange={(event) =>
                          update("interval_minutes", Number(event.target.value))
                        }
                      />
                    </Field>
                    <Field label="Заказов в одном обращении">
                      <input
                        type="number"
                        min={1}
                        max={10}
                        value={form.batch_limit || 5}
                        onChange={(event) =>
                          update("batch_limit", Number(event.target.value))
                        }
                      />
                    </Field>
                  </div>
                  <Choice
                    title="Только выданные заказы"
                    text="Для автоматического обращения требуется подтверждённая выдача в приложении."
                    checked={form.only_delivered !== false}
                    onChange={(value) => update("only_delivered", value)}
                  />
                </PluginPart>
                <PluginPart section="messages">
                  <Field
                    label="Текст обращения"
                    hint="{order_ids} заменится номерами выбранных заказов."
                  >
                    <textarea
                      value={form.message || ""}
                      onChange={(event) =>
                        update("message", event.target.value)
                      }
                    />
                  </Field>
                </PluginPart>
              </>
            )}
            {kind !== "steam-rent" && kind !== "support-tickets" && (
              <PluginPart section="settings">
                <Choice
                  title="Покупатель подтверждает получателя"
                  text="Перед выдачей нужно отправить + в чат. Получателя можно изменить до подтверждения."
                  checked={!!form.confirm_target}
                  onChange={(value) => update("confirm_target", value)}
                />
              </PluginPart>
            )}
            {["auto-smm", "steam-points"].includes(kind) && (
              <PluginPart section="settings">
                <Choice
                  title="Скрывать лоты при низком балансе"
                  text="Плагин скрывает только свои привязанные лоты и возвращает их после пополнения."
                  checked={!!form.hide_on_low_balance}
                  onChange={(value) => update("hide_on_low_balance", value)}
                />
                <Field label="Минимальный баланс поставщика">
                  <input
                    type="number"
                    min={0}
                    step={0.01}
                    value={form.low_balance_threshold ?? 0}
                    onChange={(event) =>
                      update("low_balance_threshold", event.target.value)
                    }
                  />
                </Field>
              </PluginPart>
            )}
            {kind !== "support-tickets" && (
              <PluginPart section="messages">
                <div className="adapted-messages">
                  <p className="plugin-field-help">
                    Переменные:{" "}
                    {"{order_id}, {quantity}, {buyer}, {target}, {expires}"}.
                    Плагин подставит данные заказа.
                  </p>
                  <Field
                    label={
                      kind === "steam-rent"
                        ? "Перед фотопроверкой"
                        : "После оплаты"
                    }
                    hint={
                      kind === "steam-rent"
                        ? "Отправляется только при включённой фотопроверке."
                        : "{order_id}, {quantity}, {buyer}"
                    }
                  >
                    <textarea
                      value={form.start_message || ""}
                      onChange={(event) =>
                        update("start_message", event.target.value)
                      }
                    />
                  </Field>
                  <Field
                    label={
                      kind === "steam-rent"
                        ? "Дополнение к данным аренды"
                        : "После выполнения"
                    }
                    hint={
                      kind === "auto-stars"
                        ? "Отправляется после подтверждения платежа TON. Проверка зачисления в Telegram не выполняется."
                        : kind === "steam-rent"
                          ? "Добавится к логину, паролю и сроку аренды."
                          : undefined
                    }
                  >
                    <textarea
                      value={form.success_message || ""}
                      onChange={(event) =>
                        update("success_message", event.target.value)
                      }
                    />
                  </Field>
                </div>
              </PluginPart>
            )}
            {managed &&
              (managed === "wallet" ||
                (managed === "connection" && kind !== "auto-stars")) && (
                <div className="plugin-connection-check">
                  <Button
                    secondary
                    disabled={testing || dirty || action.isPending}
                    onClick={() => void test()}
                  >
                    {testing ? (
                      <LoaderCircle size={16} className="spin" />
                    ) : (
                      <RefreshCw size={16} />
                    )}{" "}
                    {kind === "auto-stars"
                      ? "Проверить кошелёк TON"
                      : "Проверить подключение"}
                  </Button>
                  <span className="muted">
                    {dirty
                      ? "Применяем изменения…"
                      : "Проверка не создаёт заказов"}
                  </span>
                  {balances.map((row) => (
                    <div className="adapted-balance" key={row.name}>
                      <Wallet size={16} />
                      <span>{row.name}</span>
                      <strong>
                        {row.balance} {row.currency}
                      </strong>
                    </div>
                  ))}
                </div>
              )}
            <AutoSaveStatus state={auto} />
          </section>
          <aside className="adapted-guide" hidden={!!managed}>
            <div className="panel">
              <div className="adapted-section-title">
                <HeartHandshake size={20} />
                <h3>Быстрый запуск</h3>
              </div>
              <ol>
                <li>
                  <b>Подключите сервис</b>
                  <span>Заполните настройки и сохраните их.</span>
                </li>
                <li>
                  <b>
                    {kind === "support-tickets"
                      ? "Проверьте доступ"
                      : "Привяжите товары"}
                  </b>
                  <span>
                    {kind === "support-tickets"
                      ? "Кнопка проверки не создаёт тикеты."
                      : "Укажите, какие лоты будет обрабатывать модуль."}
                  </span>
                </li>
                <li>
                  <b>Включите плагин</b>
                  <span>Кнопка включения находится над настройками.</span>
                </li>
              </ol>
              <Button
                secondary
                disabled={testing || dirty || action.isPending}
                onClick={() => void test()}
              >
                {testing ? (
                  <LoaderCircle size={16} className="spin" />
                ) : (
                  <RefreshCw size={16} />
                )}{" "}
                Проверить подключение
              </Button>
              {balances.map((row) => (
                <div className="adapted-balance" key={row.name}>
                  <Wallet size={16} />
                  <span>{row.name}</span>
                  <strong>
                    {row.balance} {row.currency}
                  </strong>
                </div>
              ))}
            </div>
            <div className="adapted-safety">
              <ShieldCheck size={19} />
              <p>
                Если результат покупки неизвестен, плагин остановит повторную
                отправку. Найдите заказ в истории и проверьте его у поставщика.
              </p>
            </div>
          </aside>
        </div>
      )}
      {tab === "bindings" && (
        <section className="panel adapted-list">
          <div className="panel-head">
            <div>
              <h3>Привязанные лоты</h3>
              <p className="muted">
                Выберите свои товары и задайте количество за одну оплаченную
                единицу.
              </p>
            </div>
            <Button
              onClick={() => {
                setEditingBinding(null);
                setBinding({
                  lot_id: 0,
                  quantity:
                    kind === "auto-stars"
                      ? 50
                      : kind === "steam-points"
                        ? 1000
                        : 1,
                  provider_id: "",
                  service_id: "",
                  is_extension: false,
                  is_active: true,
                });
                setServices([]);
                setServiceSearch("");
                setBindingOpen(true);
              }}
            >
              <Plus size={15} /> Привязать лот
            </Button>
          </div>
          {query.data?.bindings.length ? (
            query.data.bindings.map((row) => (
              <div className="adapted-binding" key={row.id}>
                <span className="adapted-binding-icon">
                  <Link2 size={20} />
                </span>
                <div>
                  <strong>{row.title}</strong>
                  <small>
                    FunPay #{row.funpay_item_id} · {row.quantity}{" "}
                    {kind === "steam-rent"
                      ? "ч."
                      : kind === "auto-stars"
                        ? "Stars"
                        : kind === "steam-points"
                          ? "очков"
                          : "ед."}{" "}
                    {row.is_extension ? "· Продление" : ""}
                    {row.service_id && kind === "auto-smm"
                      ? ` · Услуга ${row.service_id}`
                      : ""}
                  </small>
                </div>
                <span className="adapted-state">
                  {row.is_active ? "Включён" : "Приостановлен"}
                </span>
                <button
                  className="icon-button"
                  title="Настроить привязку"
                  onClick={() => {
                    setEditingBinding(row.id);
                    setBinding({
                      lot_id: row.lot_id,
                      quantity: row.quantity,
                      provider_id: row.provider_id,
                      service_id: row.service_id,
                      is_extension: row.is_extension,
                      is_active: row.is_active,
                    });
                    setLotQuery(row.funpay_item_id);
                    setLotResolved({
                      lot_id: row.lot_id,
                      funpay_lot_id: row.funpay_item_id,
                      title: row.title,
                      price: "",
                      category_title: "",
                      amount: null,
                      is_active: true,
                      account_id: 0,
                      account: "",
                    });
                    setLotError("");
                    setServices([]);
                    setServiceSearch("");
                    setBindingOpen(true);
                  }}
                >
                  <Settings2 size={16} />
                </button>
                <Confirm
                  trigger={
                    <button
                      className="icon-button danger"
                      title="Удалить связь"
                    >
                      <Trash2 size={16} />
                    </button>
                  }
                  title="Удалить связь с лотом?"
                  text="Новые заказы этого лота больше не будут попадать в плагин."
                  onConfirm={() =>
                    action.mutateAsync({
                      path: `${base}/bindings/${row.id}`,
                      method: "DELETE",
                      success: "Связь удалена",
                    })
                  }
                />
              </div>
            ))
          ) : (
            <div className="adapted-empty">
              <Link2 size={30} />
              <h3>Пока нет привязанных лотов</h3>
              <p>
                Добавьте первый товар, чтобы настроить автоматическую выдачу.
              </p>
            </div>
          )}
        </section>
      )}
      {tab === "inventory" && (
        <section className="panel adapted-list">
          <div className="panel-head">
            <div>
              <h3>Пул Steam-аккаунтов</h3>
              <p className="muted">
                Пароли и секреты maFile не выводятся в общем списке.
              </p>
            </div>
            <Button
              onClick={() => {
                setInventoryForm(emptyAccount);
                setEditingAccount(null);
                setInventoryOpen(true);
              }}
            >
              <PackagePlus size={16} /> Добавить аккаунт
            </Button>
          </div>
          <div className="adapted-inventory-grid">
            {query.data?.inventory?.map((row) => (
              <div
                className={`adapted-inventory-card state-${row.state}`}
                key={row.id}
              >
                <div>
                  <ShieldCheck size={22} />
                  <span className="adapted-state">
                    {states[row.state] || row.state}
                  </span>
                </div>
                <h3>{row.label}</h3>
                <p>
                  {
                    query.data?.bindings.find(
                      (binding) => binding.id === row.binding_id,
                    )?.title
                  }
                </p>
                {row.last_error && (
                  <p className="inline-error">{row.last_error}</p>
                )}
                <div className="adapted-row-actions">
                  <Button
                    secondary
                    onClick={() =>
                      action.mutate({
                        path: `${base}/inventory/${row.id}/test`,
                        success: "Вход в Steam проверен",
                      })
                    }
                    disabled={action.isPending}
                  >
                    Проверить
                  </Button>
                  {["free", "frozen", "quarantine"].includes(row.state) && (
                    <Button
                      secondary
                      onClick={() => void editInventory(row.id)}
                    >
                      Изменить
                    </Button>
                  )}
                  {["free", "frozen"].includes(row.state) && (
                    <button
                      title={
                        row.state === "free" ? "Заморозить" : "Вернуть в пул"
                      }
                      className="icon-button"
                      onClick={() =>
                        action.mutate({
                          path: `${base}/inventory/${row.id}`,
                          method: "PATCH",
                          body: {
                            state: row.state === "free" ? "frozen" : "free",
                          },
                        })
                      }
                    >
                      <Snowflake size={16} />
                    </button>
                  )}
                  {["free", "frozen"].includes(row.state) && (
                    <Confirm
                      trigger={
                        <button
                          className="icon-button danger"
                          title="Удалить аккаунт из пула"
                        >
                          <Trash2 size={15} />
                        </button>
                      }
                      title="Удалить аккаунт из пула?"
                      text="Удаление доступно для аккаунтов без истории аренд. Аккаунт с историей можно заморозить."
                      onConfirm={() =>
                        action.mutateAsync({
                          path: `${base}/inventory/${row.id}`,
                          method: "DELETE",
                          success: "Аккаунт удалён",
                        })
                      }
                    />
                  )}
                  {row.state === "quarantine" && (
                    <Button
                      secondary
                      onClick={() =>
                        action.mutate({
                          path: `${base}/inventory/${row.id}/recover`,
                          success:
                            "Новый пароль проверен, аккаунт возвращён в пул",
                        })
                      }
                    >
                      Проверить смену
                    </Button>
                  )}
                </div>
              </div>
            ))}
          </div>
          {!query.data?.inventory?.length && (
            <div className="adapted-empty">
              <KeyRound size={30} />
              <h3>Добавьте первый аккаунт</h3>
              <p>Сначала создайте основной лот аренды на вкладке «Мои лоты».</p>
            </div>
          )}
        </section>
      )}
      {tab === "leases" && (
        <section className="panel adapted-list">
          <div className="panel-head">
            <h3>Аренды</h3>
          </div>
          {query.data?.leases?.map((row) => (
            <div className="adapted-job" key={row.id}>
              <div>
                <strong>{row.label}</strong>
                <span className="adapted-state">
                  {states[row.state] || row.state}
                </span>
              </div>
              <p>
                {row.buyer} · Заказ #{row.order_number}
              </p>
              <div className="adapted-lease-time">
                <Clock3 size={15} /> До {date(row.expires_at)}
              </div>
              {row.state === "active" && (
                <Confirm
                  trigger={<Button secondary>Завершить аренду</Button>}
                  title="Завершить аренду сейчас?"
                  text="Будет запущена смена пароля Steam. Покупатель потеряет доступ после успешной смены."
                  onConfirm={() =>
                    action.mutateAsync({
                      path: `${base}/leases/${row.id}/finish`,
                      success: "Завершение аренды запущено",
                    })
                  }
                />
              )}
            </div>
          ))}
          {!query.data?.leases?.length && (
            <div className="adapted-empty">
              Аренды появятся после первых заказов.
            </div>
          )}
        </section>
      )}
      {tab === "jobs" && (
        <section className="panel adapted-list">
          <div className="panel-head">
            <h3>История и очередь</h3>
            <span className="tag">Последние 100 заказов</span>
          </div>
          <div className="plugin-queue-toolbar">
            <label>
              <Search size={15} />
              <input
                aria-label="Поиск заказа плагина"
                placeholder="Номер заказа, покупатель или получатель"
                value={jobSearch}
                onChange={(event) => setJobSearch(event.target.value)}
              />
            </label>
            <select
              aria-label="Статус заказов плагина"
              value={jobFilter}
              onChange={(event) => setJobFilter(event.target.value)}
            >
              <option value="all">Все заказы</option>
              <option value="active">В работе</option>
              <option value="attention">Требуют внимания</option>
              <option value="done">Выполненные</option>
            </select>
          </div>
          {visibleJobs.map((job) => (
            <div className={`adapted-job state-${job.state}`} key={job.id}>
              <div>
                <strong>Заказ #{job.order_number}</strong>
                <span className="adapted-state">
                  {states[job.state] || job.state}
                </span>
              </div>
              <p>
                {job.buyer} · {job.quantity}{" "}
                {kind === "steam-rent" ? "ч." : "ед."} ·{" "}
                {job.target || "Получатель не указан"}
              </p>
              {job.remote_id && (
                <small className="adapted-remote-id">
                  ID поставщика: {job.remote_id}
                </small>
              )}
              {job.provider_status && (
                <small>
                  Статус поставщика: {job.provider_status}
                  {job.remains !== undefined
                    ? ` · Осталось: ${job.remains}`
                    : ""}
                </small>
              )}
              {job.last_error && (
                <p className="inline-error">{job.last_error}</p>
              )}
              <div className="adapted-row-actions">
                {["waiting_target", "confirm_target", "error"].includes(
                  job.state,
                ) &&
                  kind !== "steam-rent" && (
                    <div className="adapted-target">
                      <input
                        aria-label={`Получатель заказа ${job.order_number}`}
                        placeholder={
                          kind === "auto-stars"
                            ? "@username"
                            : "Ссылка получателя"
                        }
                        value={targets[job.id] ?? ""}
                        onChange={(event) =>
                          setTargets((old) => ({
                            ...old,
                            [job.id]: event.target.value,
                          }))
                        }
                      />
                      <Button
                        secondary
                        disabled={!targets[job.id]}
                        onClick={() =>
                          action.mutate({
                            path: `${base}/jobs/${job.id}/target`,
                            method: "PUT",
                            body: { target: targets[job.id] },
                          })
                        }
                      >
                        Указать
                      </Button>
                    </div>
                  )}
                {job.state === "confirm_target" && (
                  <Confirm
                    trigger={<Button>Подтвердить получателя</Button>}
                    title="Получатель проверен?"
                    text={`Будет запущена выдача для ${job.target}.`}
                    onConfirm={() =>
                      action.mutateAsync({
                        path: `${base}/jobs/${job.id}/confirm`,
                      })
                    }
                  />
                )}
                {job.state === "waiting_review" && (
                  <>
                    {job.review_image && (
                      <a
                        className="button secondary"
                        href={job.review_image}
                        target="_blank"
                        rel="noreferrer"
                      >
                        Открыть фото
                      </a>
                    )}
                    <Button onClick={() => operate(job.id, "approve")}>
                      Фото принято · Выдать
                    </Button>
                    <Button secondary onClick={() => operate(job.id, "reject")}>
                      Отклонить
                    </Button>
                  </>
                )}
                {job.remote_id &&
                  !["completed", "message_sending"].includes(job.state) && (
                    <Button secondary onClick={() => operate(job.id, "check")}>
                      Проверить результат
                    </Button>
                  )}
                {job.state === "error" && !job.remote_id && (
                  <Button secondary onClick={() => operate(job.id, "retry")}>
                    Повторить подготовку
                  </Button>
                )}
                {["uncertain", "attention"].includes(job.state) && (
                  <Confirm
                    trigger={<Button secondary>Выполнено вручную</Button>}
                    title="Выдача проверена и завершена?"
                    text="Подтвердите только после проверки поставщика и получения товара покупателем. Заказ будет отмечен выполненным; покупка и сообщение не отправляются."
                    onConfirm={() =>
                      action.mutateAsync({
                        path: `${base}/jobs/${job.id}/resolve`,
                        success: "Ручное выполнение подтверждено",
                      })
                    }
                  />
                )}
                {kind === "auto-smm" &&
                  job.remote_id &&
                  ["completed", "attention"].includes(job.state) && (
                    <Confirm
                      trigger={<Button secondary>Запросить рефилл</Button>}
                      title="Запросить рефилл у поставщика?"
                      text="Запрос будет отправлен один раз. Доступность зависит от услуги."
                      onConfirm={() =>
                        action.mutateAsync({
                          path: `${base}/jobs/${job.id}/refill`,
                        })
                      }
                    />
                  )}
              </div>
            </div>
          ))}
          {!!jobs.length && !visibleJobs.length && (
            <div className="plugin-empty">
              <h3>Заказы не найдены</h3>
              <p>Измените поиск или фильтр состояния.</p>
            </div>
          )}
          {!jobs.length && (
            <div className="adapted-empty">
              <Clock3 size={30} />
              <h3>Здесь появятся новые заказы</h3>
              <p>
                Плагин обрабатывает только оплаченные заказы привязанных лотов.
              </p>
            </div>
          )}
        </section>
      )}
      {["requests", "history"].includes(tab) && (
        <div className="adapted-setup">
          <section className="panel adapted-list" hidden={tab === "history"}>
            <div className="panel-head">
              <div>
                <h3>Заказы для обращения</h3>
                <p className="muted">
                  Показаны заказы с достоверной датой, подходящие под ваши
                  условия.
                </p>
              </div>
            </div>
            {query.data?.candidates?.map((row) => (
              <label className="adapted-support-order" key={row.id}>
                <input
                  type="checkbox"
                  checked={selectedOrders.includes(row.id)}
                  onChange={(event) =>
                    setSelectedOrders((old) =>
                      event.target.checked
                        ? [...old, row.id]
                        : old.filter((id) => id !== row.id),
                    )
                  }
                />
                <div>
                  <strong>
                    #{row.funpay_order_id} · {row.buyer_name}
                  </strong>
                  <span>{row.description}</span>
                  <small>{date(row.created_at)}</small>
                </div>
              </label>
            ))}
            {!query.data?.candidates?.length && (
              <div className="adapted-empty">
                Нет заказов, подходящих под условия обращения.
              </div>
            )}
            <Confirm
              trigger={
                <Button
                  disabled={
                    !selectedOrders.length ||
                    selectedOrders.length > 10 ||
                    action.isPending
                  }
                >
                  Создать обращение · {selectedOrders.length}
                </Button>
              }
              title="Отправить обращение в поддержку FunPay?"
              text="В тикет будут включены выбранные заказы и сохранённый текст обращения."
              onConfirm={async () => {
                await action.mutateAsync({
                  path: `${base}/send`,
                  body: { order_ids: selectedOrders },
                  success: "Обращение создано",
                });
                setSelectedOrders([]);
              }}
            />
          </section>
          <aside
            className="panel adapted-list"
            hidden={!!managed && tab !== "history"}
          >
            <div className="panel-head">
              <h3>История обращений</h3>
            </div>
            {query.data?.requests?.map((row) => (
              <div className="adapted-job" key={row.id}>
                <div>
                  <strong>
                    {row.remote_id
                      ? `Тикет #${row.remote_id}`
                      : `Запрос #${row.id}`}
                  </strong>
                  <span className="adapted-state">
                    {states[row.state] || row.state}
                  </span>
                </div>
                <p>{row.orders.map((id) => `#${id}`).join(", ")}</p>
                {row.last_error && (
                  <p className="inline-error">{row.last_error}</p>
                )}
                {row.remote_id && (
                  <a
                    href={`https://support.funpay.com/tickets/${row.remote_id}`}
                    target="_blank"
                    rel="noreferrer"
                  >
                    Открыть обращение
                  </a>
                )}
                {row.state === "error" && (
                  <Button
                    secondary
                    onClick={() =>
                      action.mutate({
                        path: `${base}/requests/${row.id}/retry`,
                      })
                    }
                  >
                    Повторить подготовку
                  </Button>
                )}
              </div>
            ))}
          </aside>
        </div>
      )}
      <Modal
        title={editingBinding ? "Настроить привязку" : "Привязать лот"}
        description={info.unit}
        open={bindingOpen}
        onOpenChange={setBindingOpen}
      >
        <div className="adapted-modal-form">
          <Field
            label="ID лота или ссылка"
            hint="Вставьте ID (например 81769276) или ссылку с offer=ID. Данные лота подгрузятся с вашего аккаунта FunPay."
          >
            <div className="adapted-lot-resolve">
              <input
                aria-label="ID лота или ссылка"
                placeholder="offer=81769276&location=offer или 81769276"
                value={lotQuery}
                onChange={(event) => setLotQuery(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") {
                    event.preventDefault();
                    void resolveLot();
                  }
                }}
              />
              <Button
                secondary
                disabled={lotBusy || !lotQuery.trim()}
                onClick={() => void resolveLot()}
              >
                {lotBusy ? <LoaderCircle size={15} className="spin" /> : <RefreshCw size={15} />}
                {lotBusy ? "Загружаем…" : "Загрузить"}
              </Button>
            </div>
          </Field>
          {lotError && <p className="template-error">{lotError}</p>}
          {lotResolved && (
            <div className="adapted-resolved-lot">
              <Link2 size={18} />
              <div>
                <strong>{lotResolved.title}</strong>
                <small>
                  FunPay #{lotResolved.funpay_lot_id} · {lotResolved.category_title} ·{" "}
                  {lotResolved.price} · Аккаунт: {lotResolved.account}
                  {lotResolved.is_active ? "" : " · Лот скрыт"}
                </small>
              </div>
            </div>
          )}
          {kind === "auto-smm" && (
            <>
              <Field label="Поставщик">
                <select
                  value={binding.provider_id}
                  onChange={(event) => {
                    setBinding((old) => ({
                      ...old,
                      provider_id: event.target.value,
                    }));
                    setServices([]);
                  }}
                >
                  <option value="">Выберите поставщика</option>
                  {form.providers?.map((row) => (
                    <option key={row.id} value={row.id}>
                      {row.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Button
                secondary
                disabled={!binding.provider_id || dirty}
                onClick={() => void loadServices()}
              >
                <RefreshCw size={15} /> Загрузить услуги
              </Button>
              <Field label="Найти услугу">
                <div className="adapted-service-search">
                  <Search size={15} />
                  <input
                    placeholder="Название услуги или ID"
                    value={serviceSearch}
                    onChange={(event) => setServiceSearch(event.target.value)}
                  />
                </div>
              </Field>
              {!!services.length && (
                <div className="adapted-services">
                  {services
                    .filter((row) =>
                      `${row.service} ${row.name}`
                        .toLowerCase()
                        .includes(serviceSearch.toLowerCase()),
                    )
                    .slice(0, 50)
                    .map((row) => (
                      <button
                        className={
                          String(row.service) === binding.service_id
                            ? "selected"
                            : ""
                        }
                        key={row.service}
                        onClick={() =>
                          setBinding((old) => ({
                            ...old,
                            service_id: String(row.service),
                          }))
                        }
                      >
                        <span>
                          #{row.service} · {row.name}
                        </span>
                        <small>
                          {row.rate} / 1000 · от {row.min} до {row.max}
                        </small>
                      </button>
                    ))}
                </div>
              )}
              <Field label="ID услуги">
                <input
                  value={binding.service_id}
                  onChange={(event) =>
                    setBinding((old) => ({
                      ...old,
                      service_id: event.target.value,
                    }))
                  }
                />
              </Field>
            </>
          )}
          <Field label={info.unit}>
            <input
              aria-label={info.unit}
              type="number"
              min={1}
              max={kind === "steam-rent" ? 8760 : 1000000}
              value={binding.quantity}
              onChange={(event) =>
                setBinding((old) => ({
                  ...old,
                  quantity: Number(event.target.value),
                }))
              }
            />
          </Field>
          {kind === "steam-rent" && (
            <>
              <Choice
                title="Этот лот продлевает аренду"
                text="Новый аккаунт не выдаётся — увеличивается срок текущей аренды покупателя."
                checked={binding.is_extension}
                onChange={(value) =>
                  setBinding((old) => ({ ...old, is_extension: value }))
                }
              />
              {binding.is_extension && (
                <Field label="Основной лот аренды">
                  <select
                    value={binding.service_id}
                    onChange={(event) =>
                      setBinding((old) => ({
                        ...old,
                        service_id: event.target.value,
                      }))
                    }
                  >
                    <option value="">Определить по активной аренде</option>
                    {query.data?.bindings
                      .filter(
                        (row) => !row.is_extension && row.id !== editingBinding,
                      )
                      .map((row) => (
                        <option value={row.id} key={row.id}>
                          {row.title}
                        </option>
                      ))}
                  </select>
                </Field>
              )}
            </>
          )}
          <Choice
            title="Обрабатывать новые заказы"
            text="Пауза привязки сохраняет настройки и историю."
            checked={binding.is_active}
            onChange={(value) =>
              setBinding((old) => ({ ...old, is_active: value }))
            }
          />
          <Button
            disabled={
              action.isPending || !binding.lot_id || binding.quantity < 1
            }
            onClick={() => void saveBinding()}
          >
            {editingBinding ? "Сохранить привязку" : "Привязать"}
          </Button>
        </div>
      </Modal>
      <Modal
        title={
          editingAccount ? "Изменить данные аккаунта" : "Добавить Steam-аккаунт"
        }
        description={
          editingAccount
            ? "Новые данные будут проверены входом в Steam перед возвращением в пул."
            : "Добавляйте только аккаунты, которыми вы управляете. Нужен Steam Guard и maFile."
        }
        open={inventoryOpen}
        onOpenChange={(open) => {
          setInventoryOpen(open);
          if (!open) setInventoryForm(emptyAccount);
        }}
      >
        <div className="adapted-modal-form">
          <Field label="Основной лот аренды">
            <select
              value={inventoryForm.binding_id}
              onChange={(event) =>
                setInventoryForm((old) => ({
                  ...old,
                  binding_id: Number(event.target.value),
                }))
              }
            >
              <option value={0}>Выберите лот</option>
              {query.data?.bindings
                .filter((row) => !row.is_extension)
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.title}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Название аккаунта">
            <input
              value={inventoryForm.label}
              onChange={(event) =>
                setInventoryForm((old) => ({
                  ...old,
                  label: event.target.value,
                }))
              }
              placeholder="Например: Dota 2 · аккаунт 1"
            />
          </Field>
          <div className="two-fields">
            <Field label="Логин Steam">
              <input
                value={inventoryForm.login}
                onChange={(event) =>
                  setInventoryForm((old) => ({
                    ...old,
                    login: event.target.value,
                  }))
                }
                autoComplete="off"
              />
            </Field>
            <Secret
              label="Пароль Steam"
              value={inventoryForm.password}
              onChange={(value) =>
                setInventoryForm((old) => ({ ...old, password: value }))
              }
            />
          </div>
          <label className="adapted-upload">
            <FileJson size={23} />
            <strong>Загрузить maFile</strong>
            <span>Данные файла останутся внутри приложения.</span>
            <input
              type="file"
              accept=".maFile,.mafile,.json,application/json"
              onChange={(event) => void readMaFile(event.target.files?.[0])}
            />
          </label>
          <details>
            <summary>Поля maFile вручную</summary>
            <Secret
              label="Shared secret"
              value={inventoryForm.shared_secret}
              onChange={(value) =>
                setInventoryForm((old) => ({ ...old, shared_secret: value }))
              }
            />
            <Secret
              label="Identity secret"
              value={inventoryForm.identity_secret}
              onChange={(value) =>
                setInventoryForm((old) => ({ ...old, identity_secret: value }))
              }
            />
            <Field label="Device ID">
              <input
                value={inventoryForm.device_id}
                onChange={(event) =>
                  setInventoryForm((old) => ({
                    ...old,
                    device_id: event.target.value,
                  }))
                }
              />
            </Field>
            <Field label="SteamID64">
              <input
                value={inventoryForm.steamid}
                onChange={(event) =>
                  setInventoryForm((old) => ({
                    ...old,
                    steamid: event.target.value,
                  }))
                }
              />
            </Field>
          </details>
          <Button
            disabled={
              action.isPending ||
              Object.values(inventoryForm).some((value) => !value)
            }
            onClick={() => void saveInventory()}
          >
            {action.isPending ? (
              <LoaderCircle size={16} className="spin" />
            ) : (
              <ShieldCheck size={16} />
            )}
            {editingAccount ? "Проверить и сохранить" : "Сохранить аккаунт"}
          </Button>
        </div>
      </Modal>
    </div>
  );
}
