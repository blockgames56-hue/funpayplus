import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Activity,
  BookOpen,
  Clock3,
  Coins,
  Eye,
  EyeOff,
  FilePlus2,
  KeyRound,
  Link2,
  LoaderCircle,
  RefreshCw,
  Search,
  Settings2,
  Trash2,
} from "lucide-react";
import { request, useAction, useData, date } from "./api";
import { Button, Empty, Field, Modal, Pending, Toggle } from "./ui";
import { PluginLogs } from "./plugin-workspace";
import { MessageTemplates } from "./message-templates";
import KosellStudio from "./kosell-studio";
import type { Lot } from "./types";
import type { KoConfig, KoState, Mapping, Rental } from "./kosell-types";
import "./kosell.css";

const tabs = [
  { id: "overview", label: "Обзор", icon: Activity },
  { id: "rentals", label: "Аренды", icon: Clock3 },
  { id: "lots", label: "Лоты", icon: Link2 },
  { id: "create", label: "Создать лоты", icon: FilePlus2 },
  { id: "settings", label: "Настройки", icon: Settings2 },
  { id: "events", label: "События", icon: Activity },
];
const states: Record<string, string> = {
  PURCHASING: "Закупка",
  PREPARING: "Подготовка",
  ACTIVE: "Активна",
  EXPIRED: "Завершена",
  MANUAL_CHECK: "Ручная сверка",
  WAITING_BALANCE: "Ожидание пополнения",
  DONE: "Подтверждено",
  CLAIMED: "Выполняется",
};
const noop = () => {};
const money = (value: string | number, currency = "RUB") =>
  new Intl.NumberFormat("ru-RU", {
    style: "currency",
    currency,
    maximumFractionDigits: 2,
  }).format(Number(value));

export default function KosellPlugin() {
  const query = useData<KoState>(
    "kosell-workspace",
    "/plugins/kosell/workspace",
  );
  const [params, setParams] = useSearchParams();
  const legacy: Record<string, string> = {
    catalog: "settings",
    studio: "lots",
    leases: "rentals",
    templates: "settings",
    logs: "events",
  };
  const section =
    legacy[params.get("section") || ""] || params.get("section") || "overview";
  const active = tabs.some((t) => t.id === section) ? section : "overview";
  const change = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("section", value);
    setParams(next);
  };
  const action = useAction();
  const [docs, setDocs] = useState(false);
  const state = query.data;
  return (
    <div className="kosell-shell">
      <div className="kosell-toolbar">
        <span className="kosell-status">
          <i className={state?.settings.enabled ? "on" : ""} />
          {state?.settings.enabled
            ? "Автовыдача работает"
            : "Автовыдача остановлена"}
        </span>
        <div>
          <Button secondary onClick={() => setDocs(true)}>
            <BookOpen size={16} />
            Инструкция
          </Button>
          <Button
            secondary
            disabled={action.isPending || !state?.settings.has_api_key}
            onClick={() =>
              action.mutate({
                path: "/plugins/kosell/refresh",
                success: "Баланс и каталог обновлены",
              })
            }
          >
            <RefreshCw
              size={16}
              className={action.isPending ? "animate-spin" : ""}
            />
            Обновить состояние
          </Button>
        </div>
      </div>
      <nav className="kosell-tabs" aria-label="Разделы KoSell">
        {tabs.map((tab) => (
          <button
            type="button"
            key={tab.id}
            aria-current={active === tab.id ? "page" : undefined}
            className={active === tab.id ? "active" : ""}
            onClick={() => change(tab.id)}
          >
            <tab.icon size={16} />
            {tab.label}
          </button>
        ))}
      </nav>
      {query.isLoading ? (
        <Pending />
      ) : query.isError ? (
        <Empty
          title="Не удалось загрузить KoSell"
          text={query.error.message}
          action={
            <Button onClick={() => void query.refetch()}>Повторить</Button>
          }
        />
      ) : (
        state && (
          <>
            {active === "overview" && (
              <Overview state={state} change={change} />
            )}
            {active === "rentals" && <Rentals state={state} />}
            {active === "lots" && <Lots state={state} />}
            {active === "create" && <KosellStudio state={state} />}
            {active === "settings" && (
              <Settings
                state={state}
                messagesInitially={params.get("section") === "templates"}
              />
            )}
            {active === "events" && (
              <div className="kosell-events">
                <PluginLogs id="kosell" />
              </div>
            )}
          </>
        )
      )}
      <Modal
        title="Как работает KoSell Rental"
        description="Подключение, лоты и автоматическая выдача в вашем приложении."
        open={docs}
        onOpenChange={setDocs}
      >
        <div className="kosell-docs">
          <ol>
            <li>
              В настройках сохраните API-ключ и проверьте подключение. Кошельки
              RUB и USD раздельные.
            </li>
            <li>
              Привяжите существующие лоты или создайте новые через студию.
              Проверьте цену и срок в предпросмотре.
            </li>
            <li>
              Выберите аккаунты FunPay и включите плагин. Новые лоты создаются
              выключенными; синхронизация включит их после проверки наличия и
              цены.
            </li>
            <li>
              После оплаты выдаётся аккаунт. Команды покупателя:{" "}
              <code>!код</code>, <code>!time</code>,{" "}
              <code>!kosell продлить логин</code>. Для продления нужно в течение
              20 минут повторно купить тот же товар.
            </li>
            <li>
              Таймаут списания или создания лота требует ручной сверки.
              Повторное нажатие не повторяет неоднозначную операцию.
            </li>
          </ol>
          <p>
            Цены ваших лотов FunPay ведутся в RUB. Валюта кошелька KoSell
            выбирается отдельно. Подтверждённые расходы и выручка считаются
            раздельно по валютам, без выдуманной конвертации.
          </p>
          <a
            href="https://kosell.store/developers"
            target="_blank"
            rel="noreferrer"
          >
            Официальная документация KoSell ↗
          </a>
        </div>
      </Modal>
    </div>
  );
}

function Overview({
  state,
  change,
}: {
  state: KoState;
  change: (value: string) => void;
}) {
  const active = state.rentals.filter((r) => r.status === "ACTIVE").length;
  return (
    <div className="kosell-stack">
      <div className="kosell-metrics">
        {[
          {
            icon: Activity,
            label: "Плагин",
            value: state.settings.enabled ? "Работает" : "Остановлен",
          },
          {
            icon: KeyRound,
            label: "Подключение",
            value: state.balance
              ? "Проверено"
              : state.settings.has_api_key
                ? "Ключ сохранён"
                : "Не настроено",
          },
          {
            icon: Coins,
            label: "Баланс KoSell",
            value: state.balance
              ? money(
                  state.settings.currency === "RUB"
                    ? state.balance.balance_rub
                    : state.balance.balance_usd,
                  state.settings.currency,
                )
              : "—",
          },
          {
            icon: Clock3,
            label: "Аренды",
            value: `${active} активных / ${state.rentals.length} в журнале`,
          },
        ].map((item) => (
          <article className="panel" key={item.label}>
            <item.icon size={20} />
            <small>{item.label}</small>
            <strong>{item.value}</strong>
          </article>
        ))}
      </div>
      <section className="panel kosell-card">
        <h3>Финансы KoSell</h3>
        <p>
          За всё время. Выручка по заказам и продлениям с учётом возвратов.
          Расходы — подтверждённые списания KoSell. До комиссии FunPay.
        </p>
        <div className="kosell-finance">
          {Object.entries(state.finance.currencies)
            .filter(
              ([code, row]) =>
                code !== "EUR" || Number(row.revenue) || Number(row.expenses),
            )
            .map(([code, row]) => (
              <article key={code}>
                <span className="kosell-pill">{code}</span>
                <div>
                  <small>Выручка</small>
                  <strong>{money(row.revenue, code)}</strong>
                </div>
                <div>
                  <small>Расходы</small>
                  <strong>{money(row.expenses, code)}</strong>
                </div>
              </article>
            ))}
        </div>
        {state.finance.unconfirmed_costs > 0 && (
          <p className="kosell-warn">
            Для {state.finance.unconfirmed_costs} операций стоимость ещё не
            подтверждена. Они не включены в расходы.
          </p>
        )}
      </section>
      <div className="kosell-two">
        {[
          {
            title: "Аренды и выдача",
            text: "Оплаченные заказы, продления, доступ и повтор неподтверждённой отправки.",
            button: "Открыть аренды",
            section: "rentals",
          },
          {
            title: "Лоты и цены",
            text: "Привязки к каталогу, остатки и наценки. Отдельная студия поможет создать несколько сроков одной партией.",
            button: "Открыть лоты",
            section: "lots",
          },
        ].map((item) => (
          <section key={item.title} className="panel kosell-card">
            <h3>{item.title}</h3>
            <p>{item.text}</p>
            <Button secondary onClick={() => change(item.section)}>
              {item.button}
            </Button>
          </section>
        ))}
      </div>
      <section className="panel kosell-card">
        <h3>Последняя синхронизация</h3>
        <p>
          {state.sync.updated_at
            ? `${date(state.sync.updated_at)} · изменено лотов: ${state.sync.changed || 0}`
            : "Синхронизация ещё не запускалась. Проверьте подключение и привязки."}
        </p>
      </section>
    </div>
  );
}

function Rentals({ state }: { state: KoState }) {
  const action = useAction();
  const [manual, setManual] = useState(false),
    [order, setOrder] = useState("");
  const [filter, setFilter] = useState("");
  const [expanded, setExpanded] = useState(true);
  const [attach, setAttach] = useState<Rental | null>(null),
    [uid, setUid] = useState("");
  const [access, setAccess] = useState<{
    rental: Rental;
    login?: string;
    password?: string;
    code?: string;
  } | null>(null);
  const [show, setShow] = useState(false);
  const rows = state.rentals.filter((r) =>
    `${r.order_id} ${r.buyer} ${r.login} ${r.uid || ""}`
      .toLowerCase()
      .includes(filter.toLowerCase()),
  );
  const getAccess = async (rental: Rental) => {
    const result = (await action.mutateAsync({
      path: `/plugins/kosell/rentals/${rental.id}/access`,
      method: "GET",
    })) as { login: string; password: string };
    setAccess({ rental, ...result });
    setShow(false);
  };
  return (
    <div className="kosell-stack">
      <div className="kosell-row">
        <p>
          Продления, выдача и доступ к аккаунтам. Закупка не повторяется после
          таймаута.
        </p>
        <Button secondary onClick={() => setManual(true)}>
          Ручная выдача
        </Button>
      </div>
      <section className="panel kosell-card">
        <div className="kosell-row">
          <h3>Журнал аренды ({state.rentals.length})</h3>
          <Button secondary onClick={() => setExpanded(!expanded)}>
            {expanded ? "Скрыть" : "Показать"}
          </Button>
        </div>
        {expanded && (
          <>
            <Field label="Поиск аренды">
              <input
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Заказ, UID, логин или покупатель"
              />
            </Field>
            <div className="kosell-table">
              <table>
                <thead>
                  <tr>
                    <th>Заказ / покупатель</th>
                    <th>UID / логин</th>
                    <th>Лот</th>
                    <th>Срок</th>
                    <th>Состояние</th>
                    <th>Действия</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((r) => (
                    <tr key={r.id}>
                      <td>
                        <strong>#{r.order_id}</strong>
                        <small>{r.buyer}</small>
                      </td>
                      <td>
                        <code>{r.uid || "—"}</code>
                        <small>{r.login || "Подготовка доступа"}</small>
                      </td>
                      <td>{r.title}</td>
                      <td>
                        {date(r.end_time)}
                        <small>
                          {r.hours} ч · осталось {Math.ceil(r.remaining / 60)}{" "}
                          мин
                        </small>
                      </td>
                      <td>
                        <span
                          className={`kosell-pill ${r.status === "MANUAL_CHECK" ? "warn" : ""}`}
                        >
                          {states[r.status] || r.status}
                        </span>
                        {r.last_error && (
                          <small className="kosell-warn">{r.last_error}</small>
                        )}
                        {r.queue.some((q) => q.state === "pending") && (
                          <small>Ожидает отправки в FunPay</small>
                        )}
                      </td>
                      <td>
                        <div className="kosell-table-actions">
                          {r.status === "ACTIVE" && (
                            <Button
                              secondary
                              disabled={action.isPending}
                              onClick={() => void getAccess(r).catch(noop)}
                            >
                              Доступ
                            </Button>
                          )}
                          {r.queue.some(
                            (q) =>
                              q.kind === "credentials" && q.state !== "sent",
                          ) &&
                            r.status === "ACTIVE" && (
                              <Button
                                secondary
                                disabled={action.isPending}
                                onClick={() =>
                                  action.mutate({
                                    path: `/plugins/kosell/rentals/${r.id}/retry-delivery`,
                                    success: "Отправка добавлена в очередь",
                                  })
                                }
                              >
                                Повторить выдачу
                              </Button>
                            )}
                          {r.status === "MANUAL_CHECK" && (
                            <Button
                              secondary
                              onClick={() => {
                                setAttach(r);
                                setUid(r.uid || "");
                              }}
                            >
                              Сверить UID
                            </Button>
                          )}
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {!rows.length && (
                <Empty
                  title="Аренд пока нет"
                  text="Здесь появятся оплаченные заказы, срок аренды и результат выдачи."
                />
              )}
            </div>
          </>
        )}
      </section>
      {state.operations.length > 0 && (
        <section className="panel kosell-card">
          <h3>Продления и бонусы</h3>
          <div className="kosell-table">
            <table>
              <thead>
                <tr>
                  <th>Операция</th>
                  <th>Аренда</th>
                  <th>Часы</th>
                  <th>Результат</th>
                  <th>Действие</th>
                </tr>
              </thead>
              <tbody>
                {state.operations.map((op) => (
                  <tr key={op.id}>
                    <td>
                      {op.kind === "review" ? "Бонус за отзыв" : "Продление"} #
                      {op.id}
                    </td>
                    <td>#{op.rental_id}</td>
                    <td>+{op.hours}</td>
                    <td>{states[op.state] || op.state}</td>
                    <td>
                      {op.state === "MANUAL_CHECK" && (
                        <Button
                          secondary
                          disabled={action.isPending}
                          onClick={() =>
                            action.mutate({
                              path: `/plugins/kosell/operations/${op.id}/reconcile`,
                              success: "Результат сверки обновлён",
                            })
                          }
                        >
                          Сверить с KoSell
                        </Button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      <Modal
        title="Ручная выдача по оплаченному заказу"
        description="Будет проверена оплата и привязка лота. Действие запускает закупку за баланс KoSell."
        open={manual}
        onOpenChange={setManual}
      >
        <Field label="Заказ FunPay">
          <select value={order} onChange={(e) => setOrder(e.target.value)}>
            <option value="">Выберите оплаченный заказ</option>
            {state.manual_orders.map((o) => (
              <option value={o.id} key={o.id}>
                #{o.number} · {o.buyer} · {o.title}
              </option>
            ))}
          </select>
        </Field>
        <Button
          disabled={!order || action.isPending}
          onClick={() =>
            void action
              .mutateAsync({
                path: "/plugins/kosell/manual-delivery",
                body: { order_id: Number(order) },
                success: "Заказ передан в обработку",
              })
              .then(() => setManual(false))
              .catch(noop)
          }
        >
          Запустить выдачу
        </Button>
      </Modal>
      <Modal
        title="Сверка закупки по UID"
        description="Найдите существующую аренду в KoSell. Подтверждение читает данные поставщика и не повторяет покупку."
        open={!!attach}
        onOpenChange={(open) => {
          if (!open) setAttach(null);
        }}
      >
        <Field label="UID из кабинета KoSell">
          <input value={uid} onChange={(e) => setUid(e.target.value)} />
        </Field>
        <Button
          disabled={!uid || action.isPending}
          onClick={() =>
            void action
              .mutateAsync({
                path: `/plugins/kosell/rentals/${attach?.id}/attach`,
                body: { uid },
                success: "UID подтверждён",
              })
              .then(() => setAttach(null))
              .catch(noop)
          }
        >
          Подтвердить соответствие заказу
        </Button>
      </Modal>
      <Modal
        title="Доступ к аккаунту Steam"
        description="Реквизиты активной аренды. Не передавайте их посторонним."
        open={!!access}
        onOpenChange={(open) => {
          if (!open) setAccess(null);
        }}
      >
        <Field label="Логин">
          <input readOnly value={access?.login || ""} />
        </Field>
        <Field label="Пароль">
          <div className="kosell-inline">
            <input
              readOnly
              type={show ? "text" : "password"}
              value={access?.password || ""}
            />
            <Button
              secondary
              aria-label="Показать или скрыть пароль"
              onClick={() => setShow(!show)}
            >
              <Eye size={16} />
            </Button>
          </div>
        </Field>
      </Modal>
    </div>
  );
}

function Lots({ state }: { state: KoState }) {
  const allLots = useData<Lot[]>("lots", "/lots");
  const action = useAction();
  const [account, setAccount] = useState(""),
    [lot, setLot] = useState(""),
    [product, setProduct] = useState(""),
    [hours, setHours] = useState(24),
    [markup, setMarkup] = useState(40),
    [autoPrice, setAutoPrice] = useState(false),
    [edit, setEdit] = useState<Mapping | null>(null);
  const [search, setSearch] = useState(""),
    [page, setPage] = useState(0),
    [selected, setSelected] = useState<number[]>([]),
    [remove, setRemove] = useState(false),
    [newMarkup, setNewMarkup] = useState(40);
  const [preview, setPreview] = useState<{
    id: string;
    rows: {
      binding_id: number;
      title: string;
      before: string;
      after: string;
    }[];
  } | null>(null);
  const items = state.catalog.items;
  const filtered = state.bindings.filter((b) =>
    `${b.title} ${b.funpay_item_id} ${items.find((p) => p.id === b.product_id)?.name || ""}`
      .toLowerCase()
      .includes(search.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(filtered.length / 12)),
    visible = filtered.slice(
      Math.min(page, pages - 1) * 12,
      (Math.min(page, pages - 1) + 1) * 12,
    );
  const save = async () => {
    const saved = (await action.mutateAsync({
      path: `/plugins/kosell/bindings${edit ? `/${edit.id}` : ""}`,
      method: edit ? "PUT" : "POST",
      body: {
        lot_id: Number(lot),
        product_id: Number(product),
        hours,
        is_active: edit?.is_active ?? true,
      },
    })) as { id: number };
    await action.mutateAsync({
      path: `/plugins/kosell/bindings/${saved.id}/policy`,
      method: "PUT",
      body: { auto_price: autoPrice, markup },
      success: "Привязка и правила цены сохранены",
    });
    setEdit(null);
    setLot("");
  };
  const load = (b: Mapping) => {
    setEdit(b);
    setAccount(String(b.account_id));
    setLot(String(b.lot_id));
    setProduct(String(b.product_id));
    setHours(b.hours);
    setAutoPrice(!!b.policy.auto_price);
    setMarkup(b.policy.markup ?? 40);
  };
  return (
    <div className="kosell-stack">
      <section className="panel kosell-card">
        <div className="kosell-row">
          <h3>Лоты KoSell</h3>
          <Button
            secondary
            disabled={action.isPending || !state.settings.enabled}
            onClick={() =>
              action.mutate({
                path: "/plugins/kosell/sync",
                success: "Наличие и цены синхронизированы",
              })
            }
          >
            <RefreshCw size={16} />
            Синхронизировать лоты и цены
          </Button>
        </div>
        <p>
          Остатки проверяются по интервалам из настроек, цены — каждые 5 минут.
          Ручное выключение в приложении сохраняется. Выбранная наценка
          применяется только при включённом обновлении цены.
        </p>
        <h4>
          {edit ? `Изменить привязку #${edit.id}` : "Добавить существующий лот"}
        </h4>
        <div className="kosell-form-grid three">
          <Field label="Аккаунт FunPay">
            <select
              value={account}
              onChange={(e) => {
                setAccount(e.target.value);
                setLot("");
              }}
            >
              <option value="">Выберите продавца</option>
              {state.accounts.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.username || a.title}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Лот FunPay">
            <select value={lot} onChange={(e) => setLot(e.target.value)}>
              <option value="">Выберите лот</option>
              {allLots.data
                ?.filter((l) => l.account_id === Number(account))
                .map((l) => (
                  <option key={l.id} value={l.id}>
                    #{l.funpay_lot_id} · {l.title}
                  </option>
                ))}
            </select>
          </Field>
          <Field label="Товар KoSell">
            <select
              value={product}
              onChange={(e) => setProduct(e.target.value)}
            >
              <option value="">Выберите товар</option>
              {items.map((p) => (
                <option value={p.id} key={p.id}>
                  {p.name} · {p.available_accounts} шт.
                </option>
              ))}
            </select>
          </Field>
          <Field label="Срок аренды, часы">
            <input
              type="number"
              min={1}
              max={8760}
              value={hours}
              onChange={(e) => setHours(Number(e.target.value))}
            />
          </Field>
          <Field label="Валюта цены FunPay">
            <input readOnly value="RUB" />
          </Field>
          <Field label="Наценка, %">
            <input
              type="number"
              min={0}
              max={10000}
              value={markup}
              onChange={(e) => setMarkup(Number(e.target.value))}
            />
          </Field>
        </div>
        <SwitchRow
          label="Обновлять цену с этой наценкой"
          hint="Наценка добавляется к точной стоимости KoSell за выбранный срок. Выключите, чтобы сохранить свою цену."
          checked={autoPrice}
          onChange={setAutoPrice}
        />
        <div className="kosell-actions">
          <Button
            disabled={
              action.isPending || !lot || !product || !Number.isInteger(hours)
            }
            onClick={() => void save().catch(noop)}
          >
            {action.isPending && (
              <LoaderCircle size={16} className="animate-spin" />
            )}
            Проверить и сохранить
          </Button>
          {edit && (
            <Button secondary onClick={() => setEdit(null)}>
              Отменить
            </Button>
          )}
        </div>
        <div className="kosell-row">
          <div className="kosell-search">
            <Search size={16} />
            <input
              aria-label="Поиск лотов KoSell"
              value={search}
              placeholder="Поиск по игре, товару или ID лота"
              onChange={(e) => {
                setSearch(e.target.value);
                setPage(0);
              }}
            />
          </div>
          <small>Найдено: {filtered.length}</small>
        </div>
        {selected.length > 0 && (
          <div className="kosell-actions">
            <span>Выбрано: {selected.length}</span>
            <Button
              secondary
              disabled={action.isPending}
              onClick={() =>
                void Promise.all(
                  selected.map((id) =>
                    action.mutateAsync({
                      path: `/plugins/kosell/bindings/${id}/enabled`,
                      method: "PUT",
                      body: { enabled: false },
                    }),
                  ),
                )
                  .then(() => setSelected([]))
                  .catch(noop)
              }
            >
              Остановить выдачу
            </Button>
            <Button secondary onClick={() => setRemove(true)}>
              <Trash2 size={15} />
              Удалить привязки
            </Button>
          </div>
        )}
        <div className="kosell-table">
          <table>
            <thead>
              <tr>
                <th>
                  <input
                    type="checkbox"
                    aria-label="Выбрать все лоты на странице"
                    checked={
                      visible.length > 0 &&
                      visible.every((b) => selected.includes(b.id))
                    }
                    onChange={(e) =>
                      setSelected(
                        e.target.checked
                          ? [
                              ...new Set([
                                ...selected,
                                ...visible.map((b) => b.id),
                              ]),
                            ]
                          : selected.filter(
                              (id) => !visible.some((b) => b.id === id),
                            ),
                      )
                    }
                  />
                </th>
                <th>Лот / продавец</th>
                <th>Товар KoSell</th>
                <th>Срок</th>
                <th>Остаток</th>
                <th>Цена и наценка</th>
                <th>Выдача</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {visible.map((b) => (
                <tr key={b.id}>
                  <td>
                    <input
                      type="checkbox"
                      aria-label={`Выбрать лот ${b.funpay_item_id}`}
                      checked={selected.includes(b.id)}
                      onChange={(e) =>
                        setSelected(
                          e.target.checked
                            ? [...selected, b.id]
                            : selected.filter((id) => id !== b.id),
                        )
                      }
                    />
                  </td>
                  <td>
                    <strong>{b.title}</strong>
                    <small>
                      #{b.funpay_item_id} ·{" "}
                      {
                        state.accounts.find((a) => a.id === b.account_id)
                          ?.username
                      }
                    </small>
                    <small>{b.lot_active ? "Лот включён" : "Лот скрыт"}</small>
                  </td>
                  <td>
                    {items.find((p) => p.id === b.product_id)?.name ||
                      `#${b.product_id}`}
                  </td>
                  <td>{b.hours} ч</td>
                  <td>
                    {items.find((p) => p.id === b.product_id)
                      ?.available_accounts ?? "—"}
                  </td>
                  <td>
                    <strong>{money(b.price)}</strong>
                    <small>
                      {b.policy.auto_price
                        ? `Наценка ${b.policy.markup}%`
                        : "Своя цена"}
                    </small>
                  </td>
                  <td>
                    <Toggle
                      label={`Выдача лота ${b.funpay_item_id}`}
                      checked={b.is_active}
                      disabled={action.isPending}
                      onChange={(enabled) =>
                        action.mutate({
                          path: `/plugins/kosell/bindings/${b.id}/enabled`,
                          method: "PUT",
                          body: { enabled },
                        })
                      }
                    />
                  </td>
                  <td>
                    <Button secondary onClick={() => load(b)}>
                      Изменить
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!visible.length && (
            <Empty
              title="Нет связанных лотов"
              text="Добавьте привязку выше или создайте лоты в студии."
            />
          )}
        </div>
        <div className="kosell-row">
          <small>
            Страница {Math.min(page + 1, pages)} из {pages}
          </small>
          <div>
            <Button
              secondary
              disabled={!page}
              onClick={() => setPage(page - 1)}
            >
              Предыдущая
            </Button>
            <Button
              secondary
              disabled={page >= pages - 1}
              onClick={() => setPage(page + 1)}
            >
              Следующая
            </Button>
          </div>
        </div>
      </section>
      <section className="panel kosell-card">
        <h3>Наценка для существующих лотов</h3>
        <p>
          Сначала покажем цены до и после. Лоты со своей ценой останутся без
          изменений. После подтверждения новая наценка сохранится, а цена FunPay
          обновится при синхронизации.
        </p>
        {!state.settings.enabled && (
          <p className="kosell-warn">
            Автовыдача остановлена. После запуска синхронизация проверит цену и
            остаток перед изменением лотов.
          </p>
        )}
        <div className="kosell-inline">
          <Field label="Новая наценка, %">
            <input
              type="number"
              min={0}
              max={10000}
              value={newMarkup}
              onChange={(e) => setNewMarkup(Number(e.target.value))}
            />
          </Field>
          <Button
            secondary
            disabled={action.isPending}
            onClick={() =>
              void action
                .mutateAsync({
                  path: "/plugins/kosell/markup/preview",
                  body: { markup: newMarkup, binding_ids: selected },
                })
                .then((value) => setPreview(value as typeof preview))
                .catch(noop)
            }
          >
            Показать цены до и после
          </Button>
        </div>
      </section>
      <Modal
        title="Удалить выбранные привязки?"
        description="Лоты FunPay и действующие аренды сохранятся. Автоматическая обработка новых заказов по этим связям прекратится."
        open={remove}
        onOpenChange={setRemove}
      >
        <p>Привязок: {selected.length}</p>
        <Button
          disabled={action.isPending}
          onClick={() =>
            void Promise.all(
              selected.map((id) =>
                action.mutateAsync({
                  path: `/plugins/kosell/bindings/${id}`,
                  method: "DELETE",
                }),
              ),
            )
              .then(() => {
                setSelected([]);
                setRemove(false);
              })
              .catch(noop)
          }
        >
          Удалить привязки
        </Button>
      </Modal>
      <Modal
        title="Предпросмотр новой наценки"
        description="Здесь показан расчёт. Нажатие «Применить» сохранит правила для следующей синхронизации."
        open={!!preview}
        onOpenChange={(open) => {
          if (!open) setPreview(null);
        }}
      >
        <div className="kosell-table">
          <table>
            <thead>
              <tr>
                <th>Лот</th>
                <th>Сейчас</th>
                <th>После</th>
              </tr>
            </thead>
            <tbody>
              {preview?.rows.map((r) => (
                <tr key={r.binding_id}>
                  <td>{r.title}</td>
                  <td>{money(r.before)}</td>
                  <td>{money(r.after)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {!preview?.rows.length && (
          <p>
            Нет лотов с включённым обновлением цены. Включите его в настройках
            привязки.
          </p>
        )}
        <Button
          disabled={action.isPending || !preview?.rows.length}
          onClick={() =>
            void action
              .mutateAsync({
                path: `/plugins/kosell/markup/${preview?.id}/apply`,
                success: "Новая наценка сохранена",
              })
              .then(() => setPreview(null))
              .catch(noop)
          }
        >
          Применить наценку
        </Button>
      </Modal>
    </div>
  );
}

function Settings({
  state,
  messagesInitially,
}: {
  state: KoState;
  messagesInitially: boolean;
}) {
  const [params, setParams] = useSearchParams();
  const active =
    params.get("settings_section") ||
    (messagesInitially ? "messages" : "connection");
  const [config, setConfig] = useState<KoConfig>(
    structuredClone(state.settings),
  );
  const [key, setKey] = useState(""),
    [show, setShow] = useState(false),
    [search, setSearch] = useState("");
  const action = useAction();
  const change = (value: string) => {
    const next = new URLSearchParams(params);
    next.set("section", "settings");
    next.set("settings_section", value);
    setParams(next);
  };
  const [dirty, setDirty] = useState(false);
  const auto = useAutoSave({ value: { config, key }, dirty,
    save: ({ config: value, key: api_key }) => { const { enabled: _, has_api_key: __, ...body } = value; return request("/plugins/kosell/settings", "PUT", { ...body, api_key }); },
    onSaved: () => setDirty(false), queryKeys: ["kosell-workspace", "plugin-catalog"] });
  const set = <K extends keyof KoConfig>(field: K, value: KoConfig[K]) => { setConfig(old => ({ ...old, [field]: value })); setDirty(true); };
  const sections = [
    { id: "connection", label: "Подключение", hint: "Ключ KoSell и проверка" },
    { id: "delivery", label: "Выдача", hint: "Заказы, бонусы, аккаунты" },
    { id: "sync", label: "Синхронизация", hint: "Наличие по играм" },
    { id: "messages", label: "Сообщения", hint: "Выдача и Steam Guard" },
  ];
  return (
    <div className="kosell-stack">
      <div className="kosell-row">
        <p>Подключение к KoSell и правила обработки заказов</p>
        <AutoSaveStatus state={auto} />
      </div>
      <div className="kosell-settings">
        <nav aria-label="Настройки KoSell">
          {sections.map((s) => (
            <button
              className={active === s.id ? "active" : ""}
              key={s.id}
              onClick={() => change(s.id)}
            >
              <strong>{s.label}</strong>
              <small>{s.hint}</small>
            </button>
          ))}
        </nav>
        <div className="kosell-settings-body">
          {active === "connection" && (
            <section className="panel kosell-card">
              <h3>Канал KoSell</h3>
              <div className="kosell-key">
                <input
                  id="kosell-api-key"
                  type={show ? "text" : "password"}
                  value={key}
                  placeholder=" "
                  autoComplete="new-password"
                  onChange={(e) => { setKey(e.target.value); setDirty(true); }}
                />
                <label htmlFor="kosell-api-key">
                  Новый API-ключ KoSell
                  {state.settings.has_api_key ? " · ключ сохранён" : ""}
                </label>
                <button
                  className="icon-button"
                  aria-label={show ? "Скрыть ключ" : "Показать ключ"}
                  onClick={() => setShow(!show)}
                >
                  {show ? <EyeOff size={17} /> : <Eye size={17} />}
                </button>
              </div>
              <div className="kosell-two">

                <Button
                  secondary
                  disabled={action.isPending || !state.settings.has_api_key}
                  onClick={() =>
                    action.mutate({
                      path: "/plugins/kosell/refresh",
                      success: "Подключение проверено, каталог обновлён",
                    })
                  }
                >
                  <RefreshCw size={16} />
                  Проверить KoSell
                </Button>
              </div>
              <Field label="Кошелёк закупки KoSell">
                <select
                  value={config.currency}
                  onChange={(e) =>
                    set("currency", e.target.value as "RUB" | "USD")
                  }
                >
                  <option>RUB</option>
                  <option>USD</option>
                </select>
              </Field>
              <p>
                Ключ шифруется перед сохранением. При смене ключа плагин
                остановится, привязки потребуется проверить заново. Действующие
                аренды сохраняют своё подключение.
              </p>
              {state.balance && (
                <div className="kosell-two">
                  <div className="kosell-soft">
                    <small>Баланс RUB</small>
                    <strong>{money(state.balance.balance_rub)}</strong>
                  </div>
                  <div className="kosell-soft">
                    <small>Баланс USD</small>
                    <strong>{money(state.balance.balance_usd, "USD")}</strong>
                  </div>
                </div>
              )}
              <h4>Каталог поставщика</h4>
              <Field label="Поиск товара KoSell">
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Название или ID игры"
                />
              </Field>
              <div className="kosell-table">
                <table>
                  <thead>
                    <tr>
                      <th>Игра</th>
                      <th>Остаток</th>
                      <th>Цена / час</th>
                      <th>Срок</th>
                    </tr>
                  </thead>
                  <tbody>
                    {state.catalog.items
                      .filter((p) =>
                        `${p.name} ${p.id}`
                          .toLowerCase()
                          .includes(search.toLowerCase()),
                      )
                      .map((p) => (
                        <tr key={p.id}>
                          <td>
                            {p.name}
                            <small>#{p.id}</small>
                          </td>
                          <td>{p.available_accounts}</td>
                          <td>{money(p.price_per_hour_rub)}</td>
                          <td>
                            {p.min_hours}–{p.max_hours} ч
                          </td>
                        </tr>
                      ))}
                  </tbody>
                </table>
                {!state.catalog.items.length && (
                  <p>
                    Сохраните API-ключ и нажмите «Проверить KoSell», чтобы
                    загрузить каталог.
                  </p>
                )}
              </div>
            </section>
          )}
          {active === "delivery" && (
            <section className="panel kosell-card">
              <h3>Правила выдачи</h3>
              <SwitchRow
                label="Автовыдача"
                hint="Обрабатывать оплаченные заказы по привязанным лотам."
                checked={config.auto_delivery}
                onChange={(value) => set("auto_delivery", value)}
              />
              <SwitchRow
                label="Бонус +1 час за отзыв"
                hint="Одно продление за отзыв с оценкой 5. Час оплачивается с баланса KoSell, только для активной аренды."
                checked={config.review_bonus}
                onChange={(value) => set("review_bonus", value)}
              />
              <SwitchRow
                label="Разрешать выдачу при изменении цены"
                hint="Выдавать аккаунт, даже если закупка дороже оплаты покупателя. Разница оплачивается с вашего баланса."
                checked={config.allow_loss}
                onChange={(value) => set("allow_loss", value)}
              />
              <SwitchRow
                label="Включать только проверенные привязки"
                checked={config.only_verified}
                onChange={(value) => set("only_verified", value)}
              />
              <hr />
              <div className="kosell-form-grid">
                <Field
                  label="Уведомлять о балансе ниже"
                  hint="0 отключает порог. Уведомление приходит один раз до пополнения в настроенный Telegram."
                >
                  <input
                    type="number"
                    min={0}
                    value={config.balance_threshold}
                    onChange={(e) =>
                      set("balance_threshold", Number(e.target.value))
                    }
                  />
                </Field>
                <Field label="Валюта порога">
                  <select
                    value={config.balance_currency}
                    onChange={(e) =>
                      set("balance_currency", e.target.value as "RUB" | "USD")
                    }
                  >
                    <option>RUB</option>
                    <option>USD</option>
                  </select>
                </Field>
              </div>
              <SwitchRow
                label="Отключать связанные лоты ниже порога"
                hint="После пополнения включатся только лоты, которые скрыл этот плагин, после проверки наличия и цены."
                checked={config.hide_low_balance}
                onChange={(value) => set("hide_low_balance", value)}
              />
              <h4>Аккаунты FunPay</h4>
              <div className="kosell-segments">
                <button
                  className={config.account_scope === "all" ? "active" : ""}
                  onClick={() => set("account_scope", "all")}
                >
                  Все аккаунты
                </button>
                <button
                  className={
                    config.account_scope === "selected" ? "active" : ""
                  }
                  onClick={() => set("account_scope", "selected")}
                >
                  Выбранные аккаунты
                </button>
              </div>
              <div className="kosell-account-list">
                {state.accounts.map((a) => (
                  <label key={a.id}>
                    <input
                      type="checkbox"
                      checked={
                        config.account_scope === "all" ||
                        config.account_ids.includes(a.id)
                      }
                      disabled={config.account_scope === "all"}
                      onChange={(e) =>
                        set(
                          "account_ids",
                          e.target.checked
                            ? [...config.account_ids, a.id]
                            : config.account_ids.filter((id) => id !== a.id),
                        )
                      }
                    />
                    <span>
                      <strong>{a.username || a.title}</strong>
                      <small>
                        #{a.id} · {a.is_active ? "работает" : "остановлен"}
                      </small>
                    </span>
                  </label>
                ))}
              </div>
            </section>
          )}
          {active === "sync" && (
            <section className="panel kosell-card">
              <h3>Синхронизация наличия</h3>
              <p>
                Проверяем каталог и скрываем лоты без свободных аккаунтов. Для
                популярных игр задайте отдельный интервал. Изменения цен
                проверяются отдельно каждые 5 минут.
              </p>
              <SwitchRow
                label="Авто-выключение лота при пустом остатке"
                checked={config.auto_hide}
                onChange={(value) => set("auto_hide", value)}
              />
              <Field
                label="Общий интервал наличия, сек."
                hint="От 10 до 3600 секунд. При ограничениях KoSell учитывается Retry-After."
              >
                <input
                  type="number"
                  min={10}
                  max={3600}
                  value={config.stock_interval}
                  onChange={(e) =>
                    set("stock_interval", Number(e.target.value))
                  }
                />
              </Field>
              <Field label="Найти игру для своего интервала">
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Название или ID игры"
                />
              </Field>
              <div className="kosell-game-intervals">
                {state.catalog.items
                  .filter((p) =>
                    `${p.name} ${p.id}`
                      .toLowerCase()
                      .includes(search.toLowerCase()),
                  )
                  .map((p) => (
                    <Field
                      key={p.id}
                      label={p.name}
                      hint="Пустое поле — общий интервал"
                    >
                      <input
                        type="number"
                        min={10}
                        max={3600}
                        value={config.game_intervals[p.id] ?? ""}
                        onChange={(e) => {
                          const next = { ...config.game_intervals };
                          if (e.target.value)
                            next[p.id] = Number(e.target.value);
                          else delete next[p.id];
                          set("game_intervals", next);
                        }}
                      />
                    </Field>
                  ))}
              </div>
              {!state.catalog.items.length && (
                <p>Загрузите каталог KoSell, чтобы выбрать отдельные игры.</p>
              )}
            </section>
          )}
          {active === "messages" && (
            <div>
              <div className="panel kosell-card">
                <h3>Сообщения покупателю и владельцу</h3>
                <p>
                  Команда !time показывает остаток времени. Для продления
                  покупатель пишет !kosell продлить логин, затем в течение 20
                  минут повторно покупает тот же товар. Часы умножаются на
                  количество купленных единиц.
                </p>
                <p>
                  Каждый шаблон сохраняется отдельно своей кнопкой. Все
                  доступные переменные показаны под полем.
                </p>
              </div>
              <MessageTemplates plugin="kosell" />
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function SwitchRow({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (value: boolean) => void;
}) {
  return (
    <div className="kosell-switch-row">
      <div>
        <strong>{label}</strong>
        {hint && <small>{hint}</small>}
      </div>
      <Toggle checked={checked} onChange={onChange} label={label} />
    </div>
  );
}
