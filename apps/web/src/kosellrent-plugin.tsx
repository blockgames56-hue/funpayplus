import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Activity,
  BookOpen,
  Clock3,
  FilePlus2,
  Link2,
  MessageSquareText,
  RefreshCw,
  Settings2,
  Trash2,
} from "lucide-react";
import { request, useAction, useData } from "./api";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { KosellSettings, KosellSettingFields } from "./kosellrent-settings";
import { KosellLots } from "./kosellrent-lots";
import { Button, Confirm, Empty, Field, Modal, Pending } from "./ui";
import { PluginLogs } from "./plugin-workspace";
import { PluginEmoji } from "./plugin-emoji";
import {
  KosellMapTools,
  KosellPricePreview,
  KosellStatistics,
} from "./kosellrent-tools";
import "./kosellrent.css";

const base = "/plugins/kosell-rent";
type Values = Record<string, unknown>;
type Product = {
  id: number;
  name: string;
  available_accounts?: number;
  price_per_hour?: number;
};
type Mapping = {
  lot_id: string;
  product_id: number;
  product_name: string;
  hours: number;
  currency: string;
  auto_created?: boolean;
};
type Rental = {
  uid: string;
  login: string;
  product_name: string;
  hours: number;
  expires_ts: number;
  order_id: string;
  password_pending?: boolean;
};
type SettingField = {
  key: string;
  label: string;
  description: string;
  type: string;
  min: number;
  max: number;
};
type LotTemplate = {
  durations: number[];
  summary_ru: string;
  summary_en: string;
  desc_ru: string;
  desc_en: string;
  price_mode: string;
  markup_percent: number;
  fixed_public_rub: number | null;
  fixed_prices?: Record<string, number>;
  usd_rub: number;
  min_price: number;
};
export type State = {
  name: string;
  version: string;
  credits: string;
  account_id: number | null;
  settings: Values;
  has_api_key: boolean;
  accounts: { id: number; title: string; active: boolean }[];
  lots: { id: number; funpay_lot_id: string; title: string }[];
  products: Product[];
  mappings: Mapping[];
  rentals: Rental[];
  hidden: Record<string, unknown>;
  balance: { balance_rub?: number; balance_usd?: number } | null;
  stats: Values;
  groups: { id: string; label: string; fields: SettingField[] }[];
  texts: {
    key: string;
    label: string;
    default: string;
    placeholders: string[];
  }[];
  operation: {
    on?: boolean;
    title?: string;
    done?: number;
    total?: number;
    created?: number;
    skipped?: number;
    failed?: number;
    cancel?: boolean;
  };
  plan: {
    product_id: number;
    game_name: string;
    hours: number;
    subcategory_id: number;
    placement?: string;
  }[];
  help: string;
  lot_help: string;
};
const tabs = [
  ["overview", "Обзор", Activity],
  ["rentals", "Аренды", Clock3],
  ["lots", "Лоты", Link2],
  ["creation", "Автосоздание", FilePlus2],
  ["settings", "Настройки", Settings2],
  ["messages", "Сообщения", MessageSquareText],
  ["events", "Журнал", Activity],
] as const;

export default function KosellRentPlugin() {
  const query = useData<State>("kosell-rent", base);
  const [params, setParams] = useSearchParams();
  const [docs, setDocs] = useState(false);
  const action = useAction();
  const state = query.data;
  useEffect(() => {
    if (!state?.operation.on) return;
    const timer = setInterval(() => void query.refetch(), 2500);
    return () => clearInterval(timer);
  }, [state?.operation.on, query.refetch]);
  const selected = params.get("section") || "overview";
  const active = tabs.some(([id]) => id === selected) ? selected : "overview";
  const change = (section: string) => {
    const next = new URLSearchParams(params);
    next.set("section", section);
    setParams(next);
  };
  if (query.isPending) return <Pending />;
  if (!state)
    return (
      <Empty
        title="Не удалось загрузить KOSell Rent"
        text={query.error?.message || "Проверьте подключение к серверу"}
        action={<Button onClick={() => void query.refetch()}>Повторить</Button>}
      />
    );
  return (
    <div className="kr-shell">
      <div className="kr-toolbar">
        <span className="kr-status">
          <i className={state.settings.enabled ? "on" : ""} />
          {state.settings.enabled
            ? "Автоаренда включена"
            : "Автоаренда выключена"}
          <small>v{state.version}</small>
        </span>
        <div className="kr-actions">
          <Button secondary onClick={() => setDocs(true)}>
            <BookOpen size={16} />
            Инструкция
          </Button>
          <Button
            secondary
            disabled={
              !state.has_api_key || action.isPending || state.operation.on
            }
            onClick={() =>
              action.mutate({
                path: `${base}/refresh`,
                success: "Баланс и каталог обновлены",
              })
            }
          >
            <RefreshCw size={16} />
            Проверить подключение
          </Button>
        </div>
      </div>
      <nav className="kr-tabs" aria-label="Разделы KOSell Rent">
        {tabs.map(([id, label]) => (
          <button
            key={id}
            type="button"
            className={id === active ? "active" : ""}
            aria-current={id === active ? "page" : undefined}
            onClick={() => change(id)}
          >
            <PluginEmoji section={id} />
            {label}
          </button>
        ))}
      </nav>
      {state.operation.on && (
        <div className="kr-card kr-progress" role="status">
          <div className="kr-toolbar">
            <strong>{state.operation.title || "Операция выполняется"}</strong>
            <Button
              secondary
              disabled={state.operation.cancel || action.isPending}
              onClick={() =>
                action.mutate({
                  path: `${base}/operations/cancel`,
                  success: "Остановка после текущего лота",
                })
              }
            >
              {state.operation.cancel ? "Останавливаем…" : "Остановить"}
            </Button>
          </div>
          <progress
            value={state.operation.done || 0}
            max={state.operation.total || 1}
          />
          <p>
            Обработано {state.operation.done || 0} из{" "}
            {state.operation.total || 0} · Успешно{" "}
            {state.operation.created || 0} · Пропущено{" "}
            {state.operation.skipped || 0} · Ошибок{" "}
            {state.operation.failed || 0}
          </p>
        </div>
      )}
      {active === "overview" && (
        <>
          <div className="kr-metrics">
            {[
              [
                "Активные аренды",
                state.rentals.filter((r) => r.expires_ts > Date.now() / 1000)
                  .length,
              ],
              ["Привязанные лоты", state.mappings.length],
              ["Баланс, ₽", state.balance?.balance_rub ?? "—"],
              ["Товары KOSell", state.products.length],
            ].map(([label, value]) => (
              <article key={label}>
                <small>{label}</small>
                <strong>{value}</strong>
              </article>
            ))}
          </div>
          <div className="kr-columns">
            <section className="kr-card">
              <span className="kr-eyebrow">Быстрый старт</span>
              <h3>Подключите аренду в три шага</h3>
              <ol className="kr-steps">
                <li>Выберите аккаунт FunPay и сохраните ключ KOSell.</li>
                <li>Проверьте подключение и привяжите свои лоты к товарам.</li>
                <li>Включите плагин кнопкой вверху страницы.</li>
              </ol>
              <div className="kr-actions">
                <Button onClick={() => change("settings")}>
                  Настроить подключение
                </Button>
                <Button secondary onClick={() => change("lots")}>
                  Привязать лоты
                </Button>
              </div>
            </section>
            <section className="kr-card">
              <span className="kr-eyebrow">Команды покупателя</span>
              <h3>Выдача и продление в чате</h3>
              <p>
                <code>!кодстим</code> — код Steam Guard.
                <br />
                <code>!продлить логин</code> — выбрать аккаунт для продления.
                <br />
                <code>!друг</code> — отдельный аккаунт при следующей покупке.
              </p>
              <p>
                Повторная оплата продлевает текущую аренду. При покупке
                нескольких штук плагин уточняет, нужны ли отдельные аккаунты.
              </p>
              <small>
                Исходный плагин {state.name} · {state.credits}
              </small>
            </section>
          </div>
          <KosellStatistics state={state} />
        </>
      )}
      {active === "settings" && (
        <KosellSettings key={state.account_id ?? "new"} state={state} />
      )}
      {active === "messages" && (
        <MessagesPanel key={state.account_id ?? "new"} state={state} />
      )}
      {active === "lots" && <KosellLots state={state} />}
      {active === "creation" && (
        <CreationPanel key={state.account_id ?? "new"} state={state} />
      )}
      {active === "rentals" && <RentalsPanel state={state} />}
      {active === "events" && <PluginLogs id="kosell-rent" />}
      <Modal
        title="KOSell Rent — инструкция"
        description="Как работает аренда через KOSell.store"
        open={docs}
        onOpenChange={setDocs}
      >
        <div className="kr-help">{state.help}</div>
      </Modal>
    </div>
  );
}

function MessagesPanel({ state }: { state: State }) {
  const original = state.settings.texts as Record<string, string>;
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [dirty, setDirty] = useState(false);
  const [filter, setFilter] = useState("all");
  const auto = useAutoSave({ value: { account_id: state.account_id, settings: { texts: { ...original, ...draft } } }, dirty,
    enabled: !!state.account_id && !state.operation.on, save: value => request(`${base}/settings`, "PUT", value), onSaved: () => setDirty(false), queryKeys: ["kosell-rent"] });
  const change = (key: string, text: string) => { setDraft(old => ({ ...old, [key]: text })); setDirty(true); };
  const category = (key: string) => key.includes("review") ? "review" : key.includes("friend") || key.includes("code") || key.includes("extend") ? "commands" : "delivery";
  return <div className="kr-stack"><section className="kr-card"><div className="kr-toolbar"><h3>💬 Сообщения покупателю</h3><AutoSaveStatus state={auto} /></div><div className="kr-settings-tabs">{[["all", "Все"], ["delivery", "Выдача"], ["commands", "Команды и продление"], ["review", "Отзывы"]].map(([id, label]) => <button type="button" key={id} className={filter === id ? "active" : ""} onClick={() => setFilter(id)}>{label}</button>)}</div></section>
  {state.texts.filter(text => filter === "all" || category(text.key) === filter).map(text => <section key={text.key} className="kr-card"><div className="kr-toolbar"><h3>{text.label}</h3><Button secondary onClick={() => change(text.key, text.default)}>Сбросить текст</Button></div><textarea aria-label={text.label} rows={5} maxLength={20000} value={draft[text.key] ?? original?.[text.key] ?? text.default} onChange={e => change(text.key, e.target.value)} /><div className="kr-actions">{text.placeholders.map(tag => <code key={tag}>{tag}</code>)}</div></section>)}</div>;
}

function RentalsPanel({ state }: { state: State }) {
  const [search, setSearch] = useState("");
  const [activeOnly, setActiveOnly] = useState(true);
  const rentals = state.rentals.filter(
    (r) =>
      (!activeOnly || r.expires_ts > Date.now() / 1000) &&
      `${r.login} ${r.uid} ${r.product_name} ${r.order_id}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <section className="kr-card">
      <div className="kr-toolbar">
        <h3>Аренды · {rentals.length}</h3>
        <div className="kr-actions">
          <input
            aria-label="Поиск аренды"
            placeholder="Логин, игра или заказ"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Button
            secondary={!activeOnly}
            onClick={() => setActiveOnly((v) => !v)}
          >
            {activeOnly ? "Только активные" : "Все аренды"}
          </Button>
        </div>
      </div>
      {!rentals.length ? (
        <Empty
          title="Аренд пока нет"
          text="Здесь появятся аккаунты, выданные исходным плагином по новым заказам FunPay."
        />
      ) : (
        <div className="kr-table">
          <table>
            <thead>
              <tr>
                <th>Игра и аккаунт</th>
                <th>Заказ</th>
                <th>Срок</th>
                <th>Действует до</th>
                <th>Состояние</th>
              </tr>
            </thead>
            <tbody>
              {rentals.map((r) => (
                <tr key={r.uid}>
                  <td>
                    <strong>{r.product_name}</strong>
                    <small>
                      {r.login} · {r.uid}
                    </small>
                  </td>
                  <td>#{r.order_id}</td>
                  <td>{r.hours} ч</td>
                  <td>
                    {r.expires_ts
                      ? new Date(r.expires_ts * 1000).toLocaleString("ru-RU")
                      : "—"}
                  </td>
                  <td>
                    {r.password_pending
                      ? "Ожидается новый пароль"
                      : r.expires_ts > Date.now() / 1000
                        ? "Активна"
                        : "Завершена"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function CreationPanel({ state }: { state: State }) {
  const saved = state.settings.ac_template as LotTemplate | null;
  const [template, setTemplate] = useState<LotTemplate>(
    saved || {
      durations: [3, 24, 72, 168],
      summary_ru: "KOSGAME — аренда на KOSTIME",
      summary_en: "KOSGAME — rent for KOSTIME",
      desc_ru: "",
      desc_en: "",
      price_mode: "markup",
      markup_percent: 50,
      fixed_public_rub: null,
      usd_rub: Number(state.settings.ac_usd_rub) || 95,
      min_price: Number(state.settings.ac_min_price) || 15,
    },
  );
  const [durations, setDurations] = useState(template.durations.join(", "));
  const [markup, setMarkup] = useState<Record<string, number>>(
    (state.settings.product_markup_percent as Record<string, number>) || {},
  );
  const [product, setProduct] = useState("");
  const [percent, setPercent] = useState("30");
  const [forceIds, setForceIds] = useState(
    ((state.settings.steam_showcase_force_ids as number[]) || []).join(", "),
  );
  const [fixedPrices, setFixedPrices] = useState<Record<string, number>>(
    template.fixed_prices || {},
  );
  const [dirty, setDirty] = useState(false);
  const [planProduct, setPlanProduct] = useState("");
  const [runHours, setRunHours] = useState("");
  const [scopeDirty, setScopeDirty] = useState(false);
  const action = useAction();
  const change = (key: keyof LotTemplate, value: unknown) => {
    setTemplate((t) => ({ ...t, [key]: value }));
    setDirty(true);
  };
  const [extras, setExtras] = useState<Record<string, unknown>>({});
  const hours = durations.split(/[,;\s]+/).filter(Boolean).map(Number);
  const auto = useAutoSave({ value: { account_id: state.account_id, settings: {
    ...extras, ac_template: { ...template, durations: hours, fixed_prices: fixedPrices },
    ac_usd_rub: template.usd_rub, ac_min_price: template.min_price,
    product_markup_percent: markup, steam_showcase_force_ids: forceIds.split(/[,;\s]+/).filter(Boolean).map(Number),
  } }, dirty, enabled: !!state.account_id && !state.operation.on && hours.length > 0 && hours.every(h => Number.isInteger(h) && h > 0 && h <= 8760) && !!template.summary_ru.trim(),
    save: value => request(`${base}/settings`, "PUT", value), onSaved: () => { setDirty(false); setScopeDirty(true); }, queryKeys: ["kosell-rent"] });
  return (
    <div className="kr-stack">
      <section className="kr-card">
        <h3 className="plugin-title-with-emoji">
          <PluginEmoji section="creation" />
          Шаблон автосоздания лотов
        </h3>
        <p>
          Шаблон применяется автоматически. Просмотрите план. Создание запускается
          отдельной кнопкой.
        </p>
        <details>
          <summary>Переменные и правила описаний</summary>
          <div className="kr-help">{state.lot_help}</div>
        </details>
        <details><summary>🎛️ Витрина и ограничения</summary><KosellSettingFields fields={(state.groups.find(g => g.id === "creation")?.fields || []).filter(f => !["ac_usd_rub", "ac_min_price"].includes(f.key))} values={{ ...state.settings, ...extras }} set={(key, value) => { setExtras(old => ({ ...old, [key]: value })); setDirty(true); }} /></details>
        <div className="kr-form">
          <Field label="Сроки аренды, часов через запятую">
            <input
              value={durations}
              onChange={(e) => {
                setDurations(e.target.value);
                setDirty(true);
              }}
            />
          </Field>
          <Field label="Расчёт цены">
            <select
              value={template.price_mode}
              onChange={(e) => change("price_mode", e.target.value)}
            >
              <option value="markup">Себестоимость + наценка</option>
              <option value="fixed">Фиксированная цена</option>
            </select>
          </Field>
          <Field
            label={
              template.price_mode === "markup"
                ? "Наценка, %"
                : "Цена для покупателя, ₽"
            }
          >
            <input
              type="number"
              min={0}
              step="any"
              value={
                template.price_mode === "markup"
                  ? template.markup_percent
                  : (template.fixed_public_rub ?? "")
              }
              onChange={(e) =>
                change(
                  template.price_mode === "markup"
                    ? "markup_percent"
                    : "fixed_public_rub",
                  Number(e.target.value),
                )
              }
            />
          </Field>
          <Field label="Курс USD/RUB">
            <input
              type="number"
              min={0.01}
              step="any"
              value={template.usd_rub}
              onChange={(e) => change("usd_rub", Number(e.target.value))}
            />
          </Field>
          <Field label="Минимальная цена, ₽"><input type="number" min={1} value={template.min_price} onChange={e => change("min_price", Number(e.target.value))} /></Field>
          {(
            [
              ["summary_ru", "Название на русском"],
              ["summary_en", "Название на английском"],
              ["desc_ru", "Описание на русском"],
              ["desc_en", "Описание на английском"],
            ] as const
          ).map(([key, label]) => (
            <Field key={key} label={label}>
              <textarea
                aria-label={label}
                rows={key.startsWith("desc") ? 6 : 3}
                value={template[key]}
                onChange={(e) => change(key, e.target.value)}
              />
            </Field>
          ))}
        </div>
        <details>
          <summary>Цены по срокам и наценка по играм</summary>
          <div className="kr-stack">
            <p>Индивидуальные значения дополняют общий шаблон цены.</p>
            <div className="kr-form">
              {durations
                .split(/[,;\s]+/)
                .filter(Boolean)
                .filter((h) => Number(h) > 0)
                .map((h, i) => (
                  <Field
                    key={`${h}-${i}`}
                    label={`Фиксированная цена за ${h} ч, ₽`}
                  >
                    <input
                      type="number"
                      min={1}
                      step="any"
                      placeholder="Общая цена"
                      value={fixedPrices[h] ?? ""}
                      onChange={(e) => {
                        setFixedPrices((p) => {
                          const next = { ...p };
                          if (e.target.value) next[h] = Number(e.target.value);
                          else delete next[h];
                          return next;
                        });
                        setDirty(true);
                      }}
                    />
                  </Field>
                ))}
            </div>
            <div className="kr-form">
              <Field label="Игра для индивидуальной наценки">
                <select
                  value={product}
                  onChange={(e) => setProduct(e.target.value)}
                >
                  <option value="">Выберите игру</option>
                  {state.products.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </Field>
              <Field label="Наценка, %">
                <input
                  type="number"
                  min={0}
                  value={percent}
                  onChange={(e) => setPercent(e.target.value)}
                />
              </Field>
            </div>
            <Button
              secondary
              disabled={!product}
              onClick={() => {
                setMarkup((m) => ({ ...m, [product]: Number(percent) }));
                setDirty(true);
              }}
            >
              Добавить наценку
            </Button>
            {Object.entries(markup).map(([id, value]) => (
              <div className="kr-toolbar" key={id}>
                <span>
                  {state.products.find((p) => String(p.id) === id)?.name ||
                    `Товар #${id}`}{" "}
                  · {value}%
                </span>
                <Button
                  secondary
                  onClick={() => {
                    setMarkup((m) => {
                      const n = { ...m };
                      delete n[id];
                      return n;
                    });
                    setDirty(true);
                  }}
                >
                  Убрать
                </Button>
              </div>
            ))}
            <Field label="Дополнительные ID товаров витрины Steam, через запятую">
              <input
                value={forceIds}
                onChange={(e) => {
                  setForceIds(e.target.value);
                  setDirty(true);
                }}
              />
            </Field>
          </div>
        </details>
        <div className="kr-form">
          <Field
            label="Игра для этого запуска"
            hint="Выбор не меняет общий шаблон."
          >
            <select
              aria-label="Игра для этого запуска"
              value={planProduct}
              onChange={(e) => {
                setPlanProduct(e.target.value);
                setScopeDirty(true);
              }}
            >
              <option value="">Все игры из шаблона</option>
              {state.products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · #{p.id}
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Часы только для этого запуска"
            hint="Оставьте пустым, чтобы использовать сроки из шаблона."
          >
            <input
              aria-label="Часы только для этого запуска"
              value={runHours}
              placeholder="Например: 3, 24, 72"
              onChange={(e) => {
                setRunHours(e.target.value);
                setScopeDirty(true);
              }}
            />
          </Field>
        </div>
        <div className="kr-actions">
          <AutoSaveStatus state={auto} />
          <Button
            secondary
            disabled={!saved || dirty || auto.isSaving || action.isPending || state.operation.on}
            onClick={() =>
              action.mutate(
                {
                  path: `${base}/plan`,
                  body: {
                    product_id: planProduct ? Number(planProduct) : null,
                    durations: runHours.trim()
                      ? runHours
                          .split(/[,;\s]+/)
                          .filter(Boolean)
                          .map(Number)
                      : null,
                  },
                  success: "План создания подготовлен",
                },
                { onSuccess: () => setScopeDirty(false) },
              )
            }
          >
            Предпросмотр плана
          </Button>
        </div>
      </section>
      <section className="kr-card">
        <div className="kr-toolbar">
          <h3>План создания · {state.plan.length}</h3>
          <Confirm
            trigger={
              <Button
                disabled={
                  !state.plan.length ||
                  dirty ||
                  scopeDirty ||
                  state.operation.on
                }
              >
                Создать недостающие лоты
              </Button>
            }
            title="Создать лоты на FunPay?"
            text={`Плагин обработает план из ${state.plan.length} позиций по сохранённому шаблону. Существующие привязки будут пропущены.`}
            onConfirm={() =>
              action.mutateAsync({
                path: `${base}/operations/create`,
                success: "Создание лотов запущено",
              })
            }
          />
        </div>
        {state.plan.length ? (
          <div className="kr-table">
            <table>
              <thead>
                <tr>
                  <th>Игра</th>
                  <th>Часы</th>
                  <th>Раздел FunPay</th>
                  <th>Размещение</th>
                </tr>
              </thead>
              <tbody>
                {state.plan.map((p, i) => (
                  <tr key={i}>
                    <td>{p.game_name}</td>
                    <td>{p.hours}</td>
                    <td>{p.subcategory_id}</td>
                    <td>
                      {p.placement === "steam"
                        ? "Витрина Steam"
                        : "Основной раздел"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p>Заполните шаблон: он применяется автоматически. Затем нажмите «Предпросмотр плана».</p>
        )}
      </section>
      <KosellPricePreview
        key={JSON.stringify([state.plan, state.settings.ac_template])}
        state={state}
        dirty={dirty || scopeDirty}
      />
      <KosellMapTools state={state} />
      <section className="kr-card">
        <h3>🔧 Обслуживание лотов плагина</h3>
        <p>
          Пересчёт обновляет цены автоматически созданных лотов. Удаление
          затрагивает все лоты, привязанные к KOSell Rent.
        </p>
        <div className="kr-actions">
          <Confirm
            trigger={
              <Button
                secondary
                disabled={!state.mappings.length || state.operation.on}
              >
                Пересчитать цены
              </Button>
            }
            title="Обновить цены лотов?"
            text={
              planProduct
                ? "Плагин применит сохранённый шаблон цен к автолотам выбранной игры."
                : "Плагин применит текущий шаблон цен к автоматически созданным лотам."
            }
            onConfirm={() =>
              action.mutateAsync({
                path: `${base}/operations/reprice`,
                body: { product_id: planProduct ? Number(planProduct) : null },
                success: "Пересчёт запущен",
              })
            }
          />
          <Confirm
            trigger={
              <Button
                secondary
                disabled={!state.mappings.length || state.operation.on}
              >
                <Trash2 size={16} />
                Удалить привязанные лоты
              </Button>
            }
            title="Удалить лоты с FunPay?"
            text={`Будут удалены ${state.mappings.length} привязанных лотов, включая добавленные вручную. Действующие аренды сохранятся.`}
            onConfirm={() =>
              action.mutateAsync({
                path: `${base}/operations/delete`,
                success: "Удаление лотов запущено",
              })
            }
          />
        </div>
      </section>
    </div>
  );
}
