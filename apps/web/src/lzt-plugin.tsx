import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { PluginPart, usePluginSection } from "./plugin-sections";
import { useEffect, useRef, useState } from "react";
import { motion } from "motion/react";
import {
  Eye,
  EyeOff,
  Search,
  ShoppingBag,
  Wallet,
  KeyRound,
  Copy,
} from "lucide-react";
import { toast } from "sonner";
import { money, request, useData } from "./api";
import { Button, Modal } from "./ui";

type Purchase = {
  id: number;
  item_id: number;
  category: string;
  game: string | null;
  title: string;
  price: string;
  currency: string;
  status: string;
  created_at: string;
};
type State = {
  settings: {
    filter_url: string;
    max_price: string;
    reserve_balance: string;
    enabled: boolean;
    has_token: boolean;
  };
  running: boolean;
  last_error: string | null;
  balance: string | null;
  today_count: number;
  today_spent: string;
  purchases: Purchase[];
};
type Form = {
  token: string;
  filter_url: string;
  max_price: number;
  reserve_balance: number;
  enabled: boolean;
};

function validFilter(value: string) {
  try {
    const url = new URL(value);
    return (
      url.protocol === "https:" &&
      url.hostname === "lzt.market" &&
      ["/steam/", "/steam", "/epicgames/", "/epicgames"].includes(
        url.pathname,
      ) &&
      !url.hash &&
      !url.username &&
      !url.password
    );
  } catch {
    return false;
  }
}

function NumberField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="lzt-number">
      <span>{label}</span>
      <div>
        <button type="button" onClick={() => onChange(Math.max(0, value - 1))}>
          −
        </button>
        <input
          type="number"
          min="0"
          step="1"
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        <button type="button" onClick={() => onChange(value + 1)}>
          +
        </button>
      </div>
    </label>
  );
}

export function LztPlugin() {
  const managed = usePluginSection();
  const query = useData<State>("lzt-plugin", "/plugins/lzt");
  const [form, setForm] = useState<Form>({
    token: "",
    filter_url: "",
    max_price: 100,
    reserve_balance: 0,
    enabled: false,
  });
  const [showToken, setShowToken] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [testing, setTesting] = useState(false);
  const [balance, setBalance] = useState<string | null>(null);
  const [credentialsOpen, setCredentialsOpen] = useState(false);
  const [credentials, setCredentials] = useState<Record<string, string> | null>(
    null,
  );
  const [showCredentials, setShowCredentials] = useState(false);
  const [loadingCredentials, setLoadingCredentials] = useState(false);
  const credentialRequest = useRef(0);
  async function revealCredentials(id: number) {
    const version = ++credentialRequest.current;
    setCredentialsOpen(true);
    setLoadingCredentials(true);
    setCredentials(null);
    setShowCredentials(false);
    try {
      const values = await request<Record<string, string>>(
        `/plugins/lzt/purchases/${id}/credentials`,
      );
      if (credentialRequest.current === version) setCredentials(values);
    } catch (error) {
      if (credentialRequest.current === version)
        toast.error((error as Error).message);
    } finally {
      if (credentialRequest.current === version) setLoadingCredentials(false);
    }
  }
  const initialized = useRef(false);
  useEffect(() => {
    if (query.data && !initialized.current) {
      initialized.current = true;
      setForm({
        token: "",
        filter_url: query.data.settings.filter_url,
        max_price: Number(query.data.settings.max_price),
        reserve_balance: Number(query.data.settings.reserve_balance),
        enabled: query.data.settings.enabled,
      });
    }
  }, [query.data]);
  const filterOk = validFilter(form.filter_url);
  const auto = useAutoSave({ value: form, dirty, enabled: managed === "connection" ? !!form.token : filterOk && form.max_price > 0,
    save: value => request(managed === "connection" ? "/plugins/lzt/connection" : "/plugins/lzt", "PUT", managed === "connection" ? { token: value.token } : { ...value, enabled: query.data?.settings.enabled ?? value.enabled }),
    onSaved: () => { setDirty(false); setForm(old => ({ ...old, token: "" })); }, queryKeys: ["lzt", "plugin-catalog"] });
  const update = (value: Form) => { setForm(value); setDirty(true); };
  return (
    <section className="lzt-plugin">
      <div
        className="lzt-config panel"
        hidden={!!managed && !["connection", "settings"].includes(managed)}
      >
        <div className="panel-head">
          <h2>Автозакупка аккаунтов</h2>
          <span className={`lzt-run-dot ${query.data?.running ? "on" : ""}`} />
        </div>
        <p className="muted">
          Поиск по фильтру LZT Market. Покупка начинается после проверки
          аккаунта площадкой.
        </p>
        <PluginPart section="connection">
          <label className="field">
            <span>Площадка</span>
            <div className="lzt-platform">
              <b>LZT</b>
              <select aria-label="Площадка">
                <option>LZT Market</option>
              </select>
            </div>
          </label>
          <label className="field">
            <span>API Ключ · scope market</span>
            <div className="lzt-token">
              <input
                type={showToken ? "text" : "password"}
                value={form.token}
                placeholder={
                  query.data?.settings.has_token
                    ? "Сохранённый ключ"
                    : "Bearer token"
                }
                onChange={(event) =>
                  update({ ...form, token: event.target.value })
                }
              />
              <button
                type="button"
                aria-label={showToken ? "Скрыть ключ" : "Показать ключ"}
                onClick={() => setShowToken(!showToken)}
              >
                {showToken ? <EyeOff size={17} /> : <Eye size={17} />}
              </button>
            </div>
          </label>
        </PluginPart>
        <PluginPart section="settings">
          <label className="field">
            <span>URL фильтра</span>
            <div
              className={`lzt-filter ${form.filter_url ? (filterOk ? "valid" : "invalid") : ""}`}
            >
              <Search size={16} />
              <input
                value={form.filter_url}
                placeholder="https://lzt.market/steam/?pmax=100"
                onChange={(event) =>
                  update({ ...form, filter_url: event.target.value })
                }
              />
            </div>
          </label>
          <div className="lzt-numbers">
            <NumberField
              label="Макс. цена"
              value={form.max_price}
              onChange={(max_price) => update({ ...form, max_price })}
            />
            <NumberField
              label="Резерв баланса"
              value={form.reserve_balance}
              onChange={(reserve_balance) =>
                update({ ...form, reserve_balance })
              }
            />
          </div>
        </PluginPart>
        {query.data?.last_error && (
          <p className="inline-error">{query.data.last_error}</p>
        )}
        <AutoSaveStatus state={auto} />
        {managed === "connection" && (
          <>
            <Button
              secondary
              disabled={
                testing || !!form.token || !query.data?.settings.has_token
              }
              onClick={async () => {
                setTesting(true);
                try {
                  const result = await request<{ balance: string }>(
                    "/plugins/lzt/test",
                    "POST",
                  );
                  setBalance(result.balance);
                  toast.success("Подключение проверено");
                } catch (error) {
                  toast.error((error as Error).message);
                } finally {
                  setTesting(false);
                }
              }}
            >
              {testing ? "Проверяем…" : "Проверить подключение"}
            </Button>
            {balance !== null && (
              <p className="text-green">Баланс: {money(balance)}</p>
            )}
          </>
        )}
        <small className="muted">
          Запросы ограничены 3 секундами. Неизвестный результат покупки требует
          ручной проверки.
        </small>
      </div>
      <div className="lzt-activity" hidden={!!managed && managed !== "history"}>
        <div className="lzt-counters">
          <div>
            <span>Куплено за сегодня</span>
            <strong>{query.data?.today_count ?? 0}</strong>
          </div>
          <div>
            <span>Потрачено</span>
            <strong>{money(query.data?.today_spent ?? 0)}</strong>
          </div>
          <div>
            <span>Баланс на маркете</span>
            <strong>
              {query.data?.balance === null || query.data?.balance === undefined
                ? "—"
                : money(query.data.balance)}
            </strong>
          </div>
        </div>
        <div className="panel lzt-feed">
          <div className="panel-head">
            <h2>Лента покупок</h2>
            <span className="tag">Последние 100</span>
          </div>
          {query.data?.purchases.length ? (
            query.data.purchases.map((item) => (
              <motion.div
                layout
                initial={{ opacity: 0, y: -12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: 0.25 }}
                key={item.id}
                className="lzt-purchase"
              >
                <span className="lzt-game">
                  <ShoppingBag size={19} />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <small>
                    {item.game || item.category} · #{item.item_id} ·{" "}
                    {item.status === "confirmed"
                      ? "Куплен"
                      : item.status === "uncertain"
                        ? "Проверьте на маркете"
                        : "Обработка"}
                  </small>
                </div>
                <b>{money(item.price)}</b>
                {item.status === "confirmed" && (
                  <Button
                    secondary
                    title="Данные купленного аккаунта"
                    onClick={() => void revealCredentials(item.id)}
                  >
                    <KeyRound size={15} /> Данные
                  </Button>
                )}
              </motion.div>
            ))
          ) : (
            <div className="lzt-empty">
              <Wallet size={25} />
              <p>Покупок пока нет</p>
            </div>
          )}
        </div>
      </div>
      <Modal
        title="Данные купленного аккаунта"
        description="Показываются только по вашему запросу. Не передавайте их посторонним."
        open={credentialsOpen}
        onOpenChange={(open) => {
          setCredentialsOpen(open);
          if (!open) {
            credentialRequest.current++;
            setCredentials(null);
            setShowCredentials(false);
            setLoadingCredentials(false);
          }
        }}
      >
        <div className="plugin-credentials">
          {loadingCredentials ? (
            <p className="muted">Загружаем данные…</p>
          ) : credentials && Object.keys(credentials).length ? (
            <>
              <Button
                secondary
                onClick={() => setShowCredentials(!showCredentials)}
              >
                {showCredentials ? <EyeOff size={15} /> : <Eye size={15} />}{" "}
                {showCredentials ? "Скрыть данные" : "Показать данные"}
              </Button>
              {Object.entries(credentials).map(([key, value]) => (
                <div className="plugin-credential" key={key}>
                  <span>
                    {(
                      {
                        login: "Логин",
                        password: "Пароль",
                        email: "Почта",
                        email_login_data: "Доступ к почте",
                        login_password: "Данные входа",
                      } as Record<string, string>
                    )[key] || key}
                  </span>
                  <div>
                    <input
                      readOnly
                      type={showCredentials ? "text" : "password"}
                      value={value}
                      aria-label={`Данные: ${key}`}
                    />
                    <button
                      className="icon-button"
                      title={`Скопировать ${key}`}
                      onClick={() =>
                        void navigator.clipboard
                          .writeText(value)
                          .then(() => toast.success("Скопировано"))
                          .catch(() => toast.error("Буфер обмена недоступен"))
                      }
                    >
                      <Copy size={15} />
                    </button>
                  </div>
                </div>
              ))}
            </>
          ) : (
            <p className="muted">
              Маркет не вернул данные аккаунта. Проверьте покупку в личном
              кабинете LZT.
            </p>
          )}
        </div>
      </Modal>
    </section>
  );
}
