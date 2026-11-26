import { useEffect, useState } from "react";
import {
  ArrowRight,
  Clock3,
  Eye,
  EyeOff,
  FileJson,
  KeyRound,
  Pause,
  Play,
  Plus,
  Settings2,
  ShieldCheck,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { date, request, useAction, useData } from "./api";
import { PluginPart, usePluginSection } from "./plugin-sections";
import { Button, Confirm, Field, Modal, Pending } from "./ui";
import type { Account, Lot } from "./types";

type Supplier = {
  id: number;
  name: string;
  api_type: "webhook" | "telegram_bot";
  endpoint_url: string;
  is_active: boolean;
  has_token?: boolean;
};
type Mapping = {
  id: number;
  account_id: number;
  funpay_item_id: string;
  supplier_id: number;
  supplier_item_id: string;
  quantity: number;
};
type Delivery = {
  id: number;
  order_id: number;
  order_number?: string;
  buyer?: string;
  status: string;
  attempts: number;
  last_error: string | null;
  created_at: string;
};
type GuardAccount = {
  id: number;
  label: string;
  login: string;
  steamid: string;
  created_at: string;
};

function SecretInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (s: string) => void;
}) {
  const [show, setShow] = useState(false);
  return (
    <div className="rent-secret">
      <input
        autoComplete="new-password"
        type={show ? "text" : "password"}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      <button
        type="button"
        aria-label={show ? "Скрыть" : "Показать"}
        onClick={() => setShow(!show)}
      >
        {show ? <EyeOff size={16} /> : <Eye size={16} />}
      </button>
    </div>
  );
}
const emptySupplier = {
  name: "",
  api_type: "webhook" as "webhook" | "telegram_bot",
  endpoint_url: "",
  token: "",
  is_active: true,
};
const emptyMapping = {
  account_id: 0,
  funpay_item_id: "",
  supplier_id: 0,
  supplier_item_id: "",
  quantity: 1,
};

export function AppRoutePlugin() {
  const suppliers = useData<Supplier[]>(
    "route-suppliers",
    "/app-route/suppliers",
  );
  const mappings = useData<Mapping[]>("route-mappings", "/app-route/mappings");
  const deliveries = useData<Delivery[]>(
    "route-deliveries",
    "/app-route/deliveries",
  );
  const accounts = useData<Account[]>("route-accounts", "/accounts");
  const lots = useData<Lot[]>("route-lots", "/lots");
  const action = useAction();
  const [supplierOpen, setSupplierOpen] = useState(false),
    [mappingOpen, setMappingOpen] = useState(false);
  const [editingSupplier, setEditingSupplier] = useState<number | null>(null),
    [editingMapping, setEditingMapping] = useState<number | null>(null);
  const [supplier, setSupplier] = useState(emptySupplier),
    [mapping, setMapping] = useState(emptyMapping);
  async function saveSupplier() {
    try {
      await action.mutateAsync({
        path: `/app-route/suppliers${editingSupplier ? `/${editingSupplier}` : ""}`,
        method: editingSupplier ? "PUT" : "POST",
        body: editingSupplier
          ? supplier
          : {
              name: supplier.name,
              api_type: supplier.api_type,
              endpoint_url: supplier.endpoint_url,
              token: supplier.token,
            },
        success: "Поставщик сохранён",
      });
      setSupplierOpen(false);
      setSupplier(emptySupplier);
    } catch {
      /* useAction */
    }
  }
  async function saveMapping() {
    try {
      await action.mutateAsync({
        path: `/app-route/mappings${editingMapping ? `/${editingMapping}` : ""}`,
        method: editingMapping ? "PUT" : "POST",
        body: mapping,
        success: "Связь сохранена",
      });
      setMappingOpen(false);
      setMapping(emptyMapping);
    } catch {
      /* useAction */
    }
  }
  if (suppliers.isLoading || mappings.isLoading) return <Pending />;
  if (suppliers.isError || mappings.isError)
    return (
      <div className="panel plugin-empty">
        <h3>Не удалось загрузить AppRoute</h3>
        <Button
          secondary
          onClick={() => {
            void suppliers.refetch();
            void mappings.refetch();
          }}
        >
          Повторить
        </Button>
      </div>
    );
  return (
    <div className="route-workspace">
      <PluginPart section="suppliers">
        <section className="panel infrastructure-panel">
          <div className="panel-head">
            <div>
              <h3>Мои поставщики</h3>
              <p className="muted">
                Ключи скрыты. Пауза останавливает новую выдачу через поставщика.
              </p>
            </div>
            <Button
              onClick={() => {
                setEditingSupplier(null);
                setSupplier(emptySupplier);
                setSupplierOpen(true);
              }}
            >
              <Plus size={15} />
              Добавить
            </Button>
          </div>
          <div className="route-table-wrap">
            <table className="route-table">
              <thead>
                <tr>
                  <th>Поставщик</th>
                  <th>Подключение</th>
                  <th>Статус</th>
                  <th>Действия</th>
                </tr>
              </thead>
              <tbody>
                {suppliers.data?.map((row) => (
                  <tr key={row.id}>
                    <td>
                      <strong>{row.name}</strong>
                      <small className="route-supplier-type">
                        {row.api_type === "webhook"
                          ? "Webhook API"
                          : "Telegram · ручная выдача"}
                      </small>
                    </td>
                    <td className="route-url">{row.endpoint_url}</td>
                    <td>
                      <span
                        className={`tag ${row.is_active ? "text-green" : ""}`}
                      >
                        {row.is_active ? "Включён" : "Пауза"}
                      </span>
                    </td>
                    <td>
                      <div className="route-row-actions">
                        <button
                          className="icon-button"
                          title="Изменить поставщика"
                          onClick={() => {
                            setEditingSupplier(row.id);
                            setSupplier({
                              name: row.name,
                              api_type: row.api_type,
                              endpoint_url: row.endpoint_url,
                              token: "",
                              is_active: row.is_active,
                            });
                            setSupplierOpen(true);
                          }}
                        >
                          <Settings2 size={15} />
                        </button>
                        <button
                          className="icon-button"
                          title={
                            row.is_active
                              ? "Приостановить поставщика"
                              : "Включить поставщика"
                          }
                          disabled={action.isPending}
                          onClick={() =>
                            action.mutate({
                              path: `/app-route/suppliers/${row.id}`,
                              method: "PUT",
                              body: {
                                name: row.name,
                                api_type: row.api_type,
                                endpoint_url: row.endpoint_url,
                                token: "",
                                is_active: !row.is_active,
                              },
                            })
                          }
                        >
                          {row.is_active ? (
                            <Pause size={15} />
                          ) : (
                            <Play size={15} />
                          )}
                        </button>
                        <Confirm
                          trigger={
                            <button
                              className="icon-button danger"
                              title="Удалить поставщика"
                            >
                              <Trash2 size={15} />
                            </button>
                          }
                          title="Удалить поставщика?"
                          text="Сначала удалите его связи с лотами. История выдачи сохранится."
                          onConfirm={() =>
                            action.mutateAsync({
                              path: `/app-route/suppliers/${row.id}`,
                              method: "DELETE",
                            })
                          }
                        />
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!suppliers.data?.length && (
              <div className="plugin-empty">
                <h3>Добавьте поставщика</h3>
                <p>
                  Укажите адрес Webhook API и ключ доступа. Затем свяжите товары
                  на отдельной вкладке.
                </p>
              </div>
            )}
          </div>
        </section>
      </PluginPart>
      <PluginPart section="bindings">
        <section className="panel infrastructure-panel">
          <div className="panel-head">
            <div>
              <h3>Связи товаров</h3>
              <p className="muted">
                Один лот FunPay → конкретный товар поставщика.
              </p>
            </div>
            <Button
              disabled={!suppliers.data?.some((row) => row.is_active)}
              onClick={() => {
                setEditingMapping(null);
                setMapping(emptyMapping);
                setMappingOpen(true);
              }}
            >
              <Plus size={15} />
              Связать товар
            </Button>
          </div>
          {mappings.data?.map((row) => (
            <div className="route-list-row" key={row.id}>
              <div>
                <strong>
                  {lots.data?.find(
                    (lot) =>
                      lot.account_id === row.account_id &&
                      lot.funpay_lot_id === row.funpay_item_id,
                  )?.title || `Лот #${row.funpay_item_id}`}
                </strong>
                <p className="muted">
                  {accounts.data?.find(
                    (account) => account.id === row.account_id,
                  )?.username || `Аккаунт #${row.account_id}`}{" "}
                  · {row.quantity} шт.
                </p>
              </div>
              <ArrowRight size={17} />
              <div>
                <strong>
                  {suppliers.data?.find((item) => item.id === row.supplier_id)
                    ?.name || "Поставщик"}
                </strong>
                <p className="muted">Товар #{row.supplier_item_id}</p>
              </div>
              <div className="route-row-actions">
                <button
                  className="icon-button"
                  title="Изменить связь"
                  onClick={() => {
                    setEditingMapping(row.id);
                    setMapping({
                      account_id: row.account_id,
                      funpay_item_id: row.funpay_item_id,
                      supplier_id: row.supplier_id,
                      supplier_item_id: row.supplier_item_id,
                      quantity: row.quantity,
                    });
                    setMappingOpen(true);
                  }}
                >
                  <Settings2 size={15} />
                </button>
                <Confirm
                  trigger={
                    <button
                      className="icon-button danger"
                      title="Удалить связь"
                    >
                      <Trash2 size={15} />
                    </button>
                  }
                  title="Удалить связь товара?"
                  text="Новые заказы этого лота больше не будут отправляться поставщику."
                  onConfirm={() =>
                    action.mutateAsync({
                      path: `/app-route/mappings/${row.id}`,
                      method: "DELETE",
                    })
                  }
                />
              </div>
            </div>
          ))}
          {!mappings.data?.length && (
            <div className="plugin-empty">
              <h3>Связей пока нет</h3>
              <p>
                {suppliers.data?.some((row) => row.is_active)
                  ? "Выберите свой лот и товар поставщика."
                  : "Сначала добавьте и включите поставщика."}
              </p>
            </div>
          )}
        </section>
      </PluginPart>
      <PluginPart section="history">
        <section className="panel infrastructure-panel">
          <div className="panel-head">
            <h3>История выдачи</h3>
            <span className="tag">Последние 100</span>
          </div>
          {deliveries.isError ? (
            <p className="inline-error">Не удалось загрузить историю.</p>
          ) : (
            deliveries.data?.map((row) => (
              <div className={`adapted-job state-${row.status}`} key={row.id}>
                <div>
                  <strong>Заказ #{row.order_number || row.order_id}</strong>
                  <span className="tag">
                    {(
                      {
                        sent: "Выдан",
                        uncertain: "Проверьте вручную",
                        manual: "Ручная выдача",
                        pending: "В очереди",
                        sending: "Отправка",
                        failed: "Ошибка",
                      } as Record<string, string>
                    )[row.status] || row.status}
                  </span>
                </div>
                <p>
                  {row.buyer} · {date(row.created_at)} · Попыток: {row.attempts}
                </p>
                {row.last_error && (
                  <p className="inline-error">{row.last_error}</p>
                )}
              </div>
            ))
          )}
          {!deliveries.isLoading && !deliveries.data?.length && (
            <div className="plugin-empty">
              <h3>Выдач пока нет</h3>
              <p>
                После оплаченных заказов здесь появятся результаты работы
                поставщиков.
              </p>
            </div>
          )}
        </section>
      </PluginPart>
      <Modal
        title={editingSupplier ? "Изменить поставщика" : "Новый поставщик"}
        description="Токен сохраняется в зашифрованном виде. Пустое поле сохраняет ранее введённый ключ."
        open={supplierOpen}
        onOpenChange={(value) => {
          setSupplierOpen(value);
          if (!value) setSupplier(emptySupplier);
        }}
      >
        <div className="route-modal-fields">
          <Field label="Название">
            <input
              value={supplier.name}
              onChange={(event) =>
                setSupplier({ ...supplier, name: event.target.value })
              }
            />
          </Field>
          <Field label="Тип подключения">
            <select
              value={supplier.api_type}
              onChange={(event) =>
                setSupplier({
                  ...supplier,
                  api_type: event.target.value as Supplier["api_type"],
                })
              }
            >
              <option value="webhook">Webhook API</option>
              <option value="telegram_bot">Telegram Bot · ручная выдача</option>
            </select>
          </Field>
          <Field label="Адрес API (HTTPS)">
            <input
              type="url"
              value={supplier.endpoint_url}
              onChange={(event) =>
                setSupplier({ ...supplier, endpoint_url: event.target.value })
              }
              placeholder="https://supplier.example/api/orders"
            />
          </Field>
          <Field label="Секретный токен">
            <input
              type="password"
              autoComplete="off"
              value={supplier.token}
              onChange={(event) =>
                setSupplier({ ...supplier, token: event.target.value })
              }
            />
          </Field>
          <Button
            disabled={
              action.isPending ||
              !supplier.name.trim() ||
              !supplier.endpoint_url
            }
            onClick={() => void saveSupplier()}
          >
            Сохранить поставщика
          </Button>
        </div>
      </Modal>
      <Modal
        title={editingMapping ? "Изменить связь" : "Связать товар"}
        description="Плагин использует ID товара, а не совпадение названий."
        open={mappingOpen}
        onOpenChange={setMappingOpen}
      >
        <div className="route-modal-fields">
          <Field label="Аккаунт FunPay">
            <select
              value={mapping.account_id}
              onChange={(event) =>
                setMapping({
                  ...mapping,
                  account_id: Number(event.target.value),
                  funpay_item_id: "",
                })
              }
            >
              <option value={0}>Выберите аккаунт</option>
              {accounts.data?.map((row) => (
                <option key={row.id} value={row.id}>
                  {row.username}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Мой лот">
            <select
              value={mapping.funpay_item_id}
              onChange={(event) =>
                setMapping({ ...mapping, funpay_item_id: event.target.value })
              }
            >
              <option value="">Выберите лот</option>
              {lots.data
                ?.filter(
                  (row) =>
                    row.account_id === mapping.account_id &&
                    !mappings.data?.some(
                      (link) =>
                        link.id !== editingMapping &&
                        link.account_id === mapping.account_id &&
                        link.funpay_item_id === row.funpay_lot_id,
                    ),
                )
                .map((row) => (
                  <option key={row.id} value={row.funpay_lot_id}>
                    {row.title} · #{row.funpay_lot_id}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Поставщик">
            <select
              value={mapping.supplier_id}
              onChange={(event) =>
                setMapping({
                  ...mapping,
                  supplier_id: Number(event.target.value),
                })
              }
            >
              <option value={0}>Выберите поставщика</option>
              {suppliers.data
                ?.filter(
                  (row) => row.is_active || row.id === mapping.supplier_id,
                )
                .map((row) => (
                  <option key={row.id} value={row.id}>
                    {row.name}
                    {!row.is_active ? " · Пауза" : ""}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="ID товара у поставщика">
            <input
              value={mapping.supplier_item_id}
              onChange={(event) =>
                setMapping({ ...mapping, supplier_item_id: event.target.value })
              }
            />
          </Field>
          <Field label="Количество на заказ">
            <input
              type="number"
              min={1}
              value={mapping.quantity}
              onChange={(event) =>
                setMapping({ ...mapping, quantity: Number(event.target.value) })
              }
            />
          </Field>
          <Button
            disabled={
              action.isPending ||
              !mapping.account_id ||
              !mapping.funpay_item_id ||
              !mapping.supplier_id ||
              !mapping.supplier_item_id.trim() ||
              mapping.quantity < 1
            }
            onClick={() => void saveMapping()}
          >
            Сохранить связь
          </Button>
        </div>
      </Modal>
    </div>
  );
}

export function OfflineActivityPlugin() {
  // Плагин «Steam Guard · Коды»: локальные коды 2FA по maFile, без входа в Steam.
  const section = usePluginSection();
  const query = useData<GuardAccount[]>(
    "offline-guard",
    "/plugins/offline/guard/accounts",
  );
  const [edit, setEdit] = useState<"new" | null>(null);
  const [code, setCode] = useState<{ label: string; value: string } | null>(
    null,
  );
  const [secret, setSecret] = useState<{ label: string; value: string } | null>(
    null,
  );
  useEffect(() => {
    if (!code) return;
    const timer = setTimeout(() => setCode(null), 30000);
    return () => clearTimeout(timer);
  }, [code]);
  useEffect(() => {
    if (!secret) return;
    const timer = setTimeout(() => setSecret(null), 30000);
    return () => clearTimeout(timer);
  }, [secret]);
  if (section && section !== "inventory") return null;
  const accounts = query.data || [];
  return (
    <>
      <div className="rent-toolbar">
        <span className="rent-note">
          Коды Steam Guard считаются локально из maFile. Вход в Steam не
          выполняется, пароль и секреты хранятся зашифрованными.
        </span>
        <Button onClick={() => setEdit("new")}>
          <Plus size={16} />
          Добавить аккаунт
        </Button>
      </div>
      <div className="rent-card-grid">
        {accounts.map((row) => (
          <article className="panel rent-account-card" key={row.id}>
            <div className="rent-card-title">
              <div className="rent-game-icon">
                <KeyRound size={24} />
              </div>
            </div>
            <h3>{row.label}</h3>
            <p>{row.login}</p>
            <small>SteamID: {row.steamid}</small>
            <div className="rent-actions">
              <Button
                secondary
                onClick={async () => {
                  try {
                    const r = await request<{ code: string; seconds: number }>(
                      `/plugins/offline/guard/accounts/${row.id}/code`,
                    );
                    setCode({
                      label: `Steam Guard · ${row.label}`,
                      value: `${r.code}\nОбновится через ${r.seconds} сек.`,
                    });
                  } catch (e) {
                    toast.error((e as Error).message);
                  }
                }}
              >
                <Clock3 size={15} />
                Код 2FA
              </Button>
              <Button
                secondary
                onClick={async () => {
                  try {
                    const r = await request<{
                      login: string;
                      password: string;
                      steamid: string;
                    }>(`/plugins/offline/guard/accounts/${row.id}/credentials`);
                    setSecret({
                      label: row.label,
                      value: `Логин: ${r.login}\nПароль: ${r.password}\nSteamID: ${r.steamid}`,
                    });
                  } catch (e) {
                    toast.error((e as Error).message);
                  }
                }}
              >
                <Eye size={15} />
                Данные
              </Button>
              <Confirm
                title="Удалить аккаунт?"
                text="Код 2FA и данные аккаунта будут удалены из приложения."
                trigger={
                  <Button secondary>
                    <Trash2 size={15} />
                  </Button>
                }
                onConfirm={async () => {
                  try {
                    await request(
                      `/plugins/offline/guard/accounts/${row.id}`,
                      "DELETE",
                    );
                    await query.refetch();
                  } catch (e) {
                    toast.error((e as Error).message);
                  }
                }}
              />
            </div>
          </article>
        ))}
      </div>
      {!accounts.length && !query.isLoading && (
        <div className="panel rent-empty">
          <KeyRound size={35} />
          <h3>Добавьте первый аккаунт Steam</h3>
          <p>
            Загрузите maFile из Steam Desktop Authenticator — коды 2FA будут
            считаться локально, без входа в Steam.
          </p>
        </div>
      )}
      <Modal
        open={edit === "new"}
        onOpenChange={(open) => {
          if (!open) setEdit(null);
        }}
        title="Аккаунт для кодов Steam Guard"
        description="Коды считаются локально из maFile: интернет и вход в Steam не нужны."
        className="rent-modal"
      >
        <GuardAccountForm
          onClose={() => setEdit(null)}
          refresh={() => void query.refetch()}
        />
      </Modal>
      <Modal
        open={!!code}
        onOpenChange={(v) => {
          if (!v) setCode(null);
        }}
        title={code?.label || "Код Steam Guard"}
        description="Окно очистится автоматически через 30 секунд."
      >
        <pre className="rent-credentials">{code?.value}</pre>
      </Modal>
      <Modal
        open={!!secret}
        onOpenChange={(v) => {
          if (!v) setSecret(null);
        }}
        title={secret?.label || "Данные аккаунта"}
        description="Окно очистится автоматически через 30 секунд."
      >
        <pre className="rent-credentials">{secret?.value}</pre>
      </Modal>
    </>
  );
}

function GuardAccountForm({
  onClose,
  refresh,
}: {
  onClose: () => void;
  refresh: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [form, setForm] = useState({
    label: "",
    login: "",
    password: "",
    mafile: "",
  });
  const set = (key: string, value: string) =>
    setForm((f) => ({ ...f, [key]: value }));
  async function save() {
    setBusy(true);
    try {
      await request("/plugins/offline/guard/accounts", "POST", form);
      toast.success("Аккаунт добавлен");
      refresh();
      onClose();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="rental-account-form">
      <div className="two-fields">
        <Field label="Название аккаунта">
          <input
            value={form.label}
            onChange={(e) => set("label", e.target.value)}
            placeholder="Мой основной · CS2"
          />
        </Field>
        <Field label="Логин Steam">
          <input
            autoComplete="off"
            value={form.login}
            onChange={(e) => set("login", e.target.value)}
          />
        </Field>
      </div>
      <Field label="Пароль Steam" hint="Только для просмотра данных аккаунта">
        <SecretInput value={form.password} onChange={(v) => set("password", v)} />
      </Field>
      <Field
        label="maFile"
        hint="Загрузите JSON из Steam Desktop Authenticator: коды 2FA считаются из него."
      >
        <label className="rent-upload">
          <FileJson size={22} />
          <span>
            {form.mafile
              ? "maFile загружен · заменить"
              : "Выбрать .maFile / .json"}
          </span>
          <input
            type="file"
            accept=".maFile,.mafile,.json,application/json"
            onChange={async (e) => {
              const file = e.target.files?.[0];
              if (!file) return;
              if (file.size > 100000) {
                toast.error("maFile больше 100 КБ");
                return;
              }
              set("mafile", await file.text());
            }}
          />
        </label>
      </Field>
      <Field label="Или вставьте JSON maFile">
        <textarea
          value={form.mafile}
          onChange={(e) => set("mafile", e.target.value)}
          rows={4}
          spellCheck={false}
          className="rent-mafile"
        />
      </Field>
      <footer className="rent-actions">
        <Button secondary onClick={onClose}>
          Отмена
        </Button>
        <Button
          disabled={busy || !form.label || !form.login || !form.mafile}
          onClick={() => void save()}
        >
          <ShieldCheck size={16} />
          {busy ? "Сохраняем…" : "Добавить аккаунт"}
        </Button>
      </footer>
    </div>
  );
}
