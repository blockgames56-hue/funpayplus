import { useSyncedSettings } from "./synced-settings";
import { AutoSaveStatus, useAutoSave } from "./auto-save";
import { PluginPart, usePluginSection } from "./plugin-sections";
import { useEffect, useRef, useState } from "react";
import {
  ArrowDownRight,
  Clipboard,
  Copy,
  Eye,
  EyeOff,
  Plus,
  Settings2,
  RefreshCw,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { request, useAction, useData } from "./api";
import { Button, Field, Modal } from "./ui";
import type { Lot } from "./types";

type DumpOptions = {
  keywords: string;
  excluded_sellers: string[];
  only_sellers: string[];
  min_rating: number;
  ignore_unrated: boolean;
  max_price: string | null;
  divider: string;
  convert_commission: boolean;
};
const defaultDumpOptions: DumpOptions = {
  keywords: "",
  excluded_sellers: [],
  only_sellers: [],
  min_rating: 0,
  ignore_unrated: false,
  max_price: null,
  divider: "0.01",
  convert_commission: true,
};

type DumpRule = {
  id: number;
  lot_id: number;
  category_url: string;
  absolute_min: string;
  step: string;
  competitor_price: string | null;
  options?: DumpOptions;
  is_enabled: boolean;
  status: string;
  last_error: string | null;
};
type RobuxSettings = {
  universe_id: number;
  game_pass_id: number;
  target_amount: number;
  cover_fee: boolean;
  has_cookie: boolean;
};
type PassCheck = {
  game_pass_id: number;
  name: string;
  actual_price: number;
  expected_price: number;
  estimated_net: number;
  matches: boolean;
  purchase_available: boolean;
};

export function AutoDumpPlugin() {
  const [ruleTab, setRuleTab] = useState("price");
  const rules = useData<DumpRule[]>("dump-rules", "/auto-dump/rules");
  const lots = useData<Lot[]>("dump-lots", "/lots");
  const action = useAction();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<number | null>(null);
  const [draft, setDraft] = useState({
    lot_id: 0,
    category_url: "",
    absolute_min: "",
    step: "1",
    is_enabled: false,
    options: defaultDumpOptions,
  });
  const [flash, setFlash] = useState<number[]>([]);
  const previous = useRef<Record<number, string>>({});
  useEffect(() => {
    if (!rules.data || !lots.data) return;
    const next: Record<number, string> = {};
    const changed: number[] = [];
    for (const row of rules.data) {
      const price = String(
        lots.data.find((lot) => lot.id === row.lot_id)?.price ?? "",
      );
      next[row.id] = price;
      if (previous.current[row.id] && previous.current[row.id] !== price)
        changed.push(row.id);
    }
    previous.current = next;
    if (changed.length) {
      setFlash(changed);
      const timer = window.setTimeout(() => setFlash([]), 500);
      return () => window.clearTimeout(timer);
    }
  }, [rules.data, lots.data]);
  const save = async () => {
    try {
      await action.mutateAsync({
        path: editing ? `/auto-dump/rules/${editing}` : "/auto-dump/rules",
        method: editing ? "PUT" : "POST",
        body: {
          ...draft,
          options: {
            ...draft.options,
            excluded_sellers: draft.options.excluded_sellers
              .map((value) => value.trim())
              .filter(Boolean),
            only_sellers: draft.options.only_sellers
              .map((value) => value.trim())
              .filter(Boolean),
          },
          absolute_min: Number(draft.absolute_min),
          step: Number(draft.step),
        },
        success: editing ? "Правило обновлено" : "Товар добавлен",
      });
      setOpen(false);
      setDraft({
        lot_id: 0,
        category_url: "",
        absolute_min: "",
        step: "1",
        is_enabled: false,
        options: defaultDumpOptions,
      });
    } catch {
      /* useAction shows the error */
    }
  };
  return (
    <section className="panel pricing-panel">
      <div className="panel-head">
        <div>
          <h2>AutoDump</h2>
          <p className="muted">
            Цены конкурентов · обновление раз в минуту · до 5 проверок
            одновременно
          </p>
        </div>
        <div className="pricing-actions">
          <Button
            secondary
            onClick={() =>
              action.mutate({
                path: "/auto-dump/scan",
                success: "Проверка завершена",
              })
            }
            disabled={action.isPending}
          >
            <RefreshCw size={15} /> Проверить
          </Button>
          <Button
            onClick={() => {
              setEditing(null);
              setDraft({
                lot_id: 0,
                category_url: "",
                absolute_min: "",
                step: "1",
                is_enabled: false,
                options: defaultDumpOptions,
              });
              setOpen(true);
            }}
          >
            <Plus size={15} /> Добавить товар
          </Button>
        </div>
      </div>
      <div className="pricing-scroll">
        <table className="pricing-table">
          <thead>
            <tr>
              <th>Товар</th>
              <th>Наш минимум</th>
              <th>Шаг</th>
              <th>Моя цена</th>
              <th>Цена конкурента</th>
              <th>Состояние</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {rules.data?.map((row) => {
              const lot = lots.data?.find((item) => item.id === row.lot_id);
              return (
                <tr key={row.id}>
                  <td>
                    <strong>{lot?.title || `Лот #${row.lot_id}`}</strong>
                    <small>#{lot?.funpay_lot_id}</small>
                  </td>
                  <td>{row.absolute_min} ₽</td>
                  <td>{row.step} ₽</td>
                  <td
                    className={`price-cell ${flash.includes(row.id) ? "price-flash" : ""}`}
                  >
                    {lot?.price ?? "—"} ₽
                  </td>
                  <td className="competitor-price">
                    {row.competitor_price ? `${row.competitor_price} ₽` : "—"}
                  </td>
                  <td>
                    <span className="tag" title={row.last_error || ""}>
                      {row.status === "error"
                        ? "Ошибка"
                        : row.status === "floor"
                          ? "Ниже минимума"
                          : row.status === "updated"
                            ? "Обновлено"
                            : row.status === "no_competitor"
                              ? "Нет данных"
                              : row.status === "promotion"
                                ? "Акция"
                                : row.status === "current"
                                  ? "Актуально"
                                  : "Ожидание"}
                    </span>
                  </td>
                  <td>
                    <div className="pricing-row-actions">
                      <button
                        className="icon-button"
                        title="Настроить правило"
                        onClick={() => {
                          setEditing(row.id);
                          setDraft({
                            lot_id: row.lot_id,
                            category_url: row.category_url,
                            absolute_min: row.absolute_min,
                            step: row.step,
                            is_enabled: row.is_enabled,
                            options: { ...defaultDumpOptions, ...row.options },
                          });
                          setOpen(true);
                        }}
                      >
                        <Settings2 size={15} />
                      </button>
                      <button
                        className={`mini-switch ${row.is_enabled ? "on" : ""}`}
                        aria-label={
                          row.is_enabled
                            ? "Выключить демпинг"
                            : "Включить демпинг"
                        }
                        aria-pressed={row.is_enabled}
                        onClick={() =>
                          action.mutate({
                            path: `/auto-dump/rules/${row.id}`,
                            method: "PATCH",
                            body: { is_enabled: !row.is_enabled },
                          })
                        }
                      />
                      <button
                        className="icon-button danger"
                        title="Удалить правило"
                        onClick={() =>
                          action.mutate({
                            path: `/auto-dump/rules/${row.id}`,
                            method: "DELETE",
                          })
                        }
                      >
                        <Trash2 size={15} />
                      </button>
                    </div>
                  </td>
                </tr>
              );
            })}
            {!rules.data?.length && (
              <tr>
                <td colSpan={7} className="muted">
                  Добавьте лот и ссылку на его категорию FunPay.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      <Modal
        title={editing ? "Настройки AutoDump" : "Новый товар AutoDump"}
        description="Правило начнёт работать после включения. Минимальная цена всегда сохраняется."
        open={open}
        onOpenChange={(value) => {
          setOpen(value);
          if (!value) setRuleTab("price");
        }}
      >
        <div className="pricing-form">
          <nav className="plugin-rule-tabs" aria-label="Категории правила">
            <button
              aria-pressed={ruleTab === "price"}
              onClick={() => setRuleTab("price")}
            >
              Товар и цена
            </button>
            <button
              aria-pressed={ruleTab === "filters"}
              onClick={() => setRuleTab("filters")}
            >
              Конкуренты
            </button>
          </nav>
          <div className="plugin-form-part" hidden={ruleTab !== "price"}>
            <Field label="Товар">
              <select
                value={draft.lot_id}
                disabled={!!editing}
                onChange={(e) => {
                  const lot = lots.data?.find(
                    (item) => item.id === Number(e.target.value),
                  );
                  setDraft({
                    ...draft,
                    lot_id: Number(e.target.value),
                    category_url: lot
                      ? `https://funpay.com/lots/${lot.node_id}/`
                      : "",
                  });
                }}
              >
                <option value={0}>Выберите лот</option>
                {lots.data
                  ?.filter(
                    (lot) =>
                      lot.id === draft.lot_id ||
                      (lot.is_active &&
                        !rules.data?.some((row) => row.lot_id === lot.id)),
                  )
                  .map((lot) => (
                    <option key={lot.id} value={lot.id}>
                      {lot.title} · #{lot.funpay_lot_id}
                    </option>
                  ))}
              </select>
            </Field>
            <Field label="Ссылка на категорию FunPay">
              <input
                type="url"
                value={draft.category_url}
                onChange={(e) =>
                  setDraft({ ...draft, category_url: e.target.value })
                }
              />
            </Field>
            <div className="two-fields">
              <Field label="Наш абсолютный минимум, ₽">
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={draft.absolute_min}
                  onChange={(e) =>
                    setDraft({ ...draft, absolute_min: e.target.value })
                  }
                />
              </Field>
              <Field label="Шаг демпинга, ₽">
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={draft.step}
                  onChange={(e) => setDraft({ ...draft, step: e.target.value })}
                />
              </Field>
            </div>
            <label className="lot-checkbox">
              <input
                type="checkbox"
                checked={draft.options.convert_commission}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    options: {
                      ...draft.options,
                      convert_commission: event.target.checked,
                    },
                  })
                }
              />{" "}
              Учитывать комиссию FunPay при сравнении цен
            </label>
            <Field
              label="Максимальная цена продавца, ₽"
              hint="Не заполняйте, если ограничение не нужно."
            >
              <input
                type="number"
                min="0.01"
                step="0.01"
                value={draft.options.max_price ?? ""}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    options: {
                      ...draft.options,
                      max_price: event.target.value || null,
                    },
                  })
                }
              />
            </Field>
          </div>
          <div className="plugin-form-part" hidden={ruleTab !== "filters"}>
            <Field
              label="Ключевые слова"
              hint="Каждая строка обязательна. Варианты в строке разделяйте |."
            >
              <textarea
                value={draft.options.keywords}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    options: { ...draft.options, keywords: event.target.value },
                  })
                }
                placeholder="steam|стим\nаккаунт"
              />
            </Field>
            <div className="two-fields">
              <Field
                label="Игнорировать продавцов"
                hint="Никнеймы через запятую."
              >
                <input
                  value={draft.options.excluded_sellers.join(", ")}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      options: {
                        ...draft.options,
                        excluded_sellers: event.target.value.split(","),
                      },
                    })
                  }
                />
              </Field>
              <Field label="Только эти продавцы">
                <input
                  value={draft.options.only_sellers.join(", ")}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      options: {
                        ...draft.options,
                        only_sellers: event.target.value.split(","),
                      },
                    })
                  }
                />
              </Field>
            </div>
            <div className="two-fields">
              <Field label="Минимальный рейтинг, звёзд">
                <input
                  type="number"
                  min={0}
                  max={5}
                  value={draft.options.min_rating}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      options: {
                        ...draft.options,
                        min_rating: Number(event.target.value),
                      },
                    })
                  }
                />
              </Field>
              <Field label="Округление цены, ₽">
                <input
                  type="number"
                  min="0.01"
                  step="0.01"
                  value={draft.options.divider}
                  onChange={(event) =>
                    setDraft({
                      ...draft,
                      options: {
                        ...draft.options,
                        divider: event.target.value,
                      },
                    })
                  }
                />
              </Field>
            </div>
            <label className="lot-checkbox">
              <input
                type="checkbox"
                checked={draft.options.ignore_unrated}
                onChange={(event) =>
                  setDraft({
                    ...draft,
                    options: {
                      ...draft.options,
                      ignore_unrated: event.target.checked,
                    },
                  })
                }
              />{" "}
              Игнорировать продавцов без рейтинга
            </label>
          </div>
          <Button
            disabled={
              action.isPending ||
              !draft.lot_id ||
              !draft.category_url ||
              Number(draft.absolute_min) <= 0 ||
              Number(draft.step) <= 0
            }
            onClick={save}
          >
            {editing ? "Сохранить правило" : "Добавить правило"}
          </Button>
        </div>
      </Modal>
    </section>
  );
}

export function AutoRobuxPlugin() {
  const managed = usePluginSection();
  const query = useData<RobuxSettings>(
    "robux-settings",
    "/auto-robux/settings",
  );
  const action = useAction();
  const [form, setForm] = useState({
    universe_id: 0,
    game_pass_id: 0,
    target_amount: 100,
    cover_fee: true,
    cookie: "",
  });
  const [showCookie, setShowCookie] = useState(false);
  const [check, setCheck] = useState<PassCheck | null>(null);
  useSyncedSettings(query.data ? {cookie: "", universe_id: query.data.universe_id, game_pass_id: query.data.game_pass_id, target_amount: query.data.target_amount, cover_fee: query.data.cover_fee} : undefined, setForm);
  const expected = form.cover_fee
    ? Math.ceil(form.target_amount / 0.7)
    : form.target_amount;
  const net = Math.floor(expected * 0.7);
  const [dirty, setDirty] = useState(false);
  const auto = useAutoSave({ value: form, dirty, enabled: managed === "connection" ? !!form.cookie : !!form.universe_id && !!form.game_pass_id && form.target_amount >= 1,
    save: value => request(managed === "connection" ? "/auto-robux/connection" : "/auto-robux/settings", "PUT", managed === "connection" ? { cookie: value.cookie } : value),
    onSaved: () => { setDirty(false); setForm(old => ({ ...old, cookie: "" })); setCheck(null); }, queryKeys: ["auto-robux", "plugin-catalog"] });
  const update = (value: typeof form) => { setForm(value); setDirty(true); };
  const verify = async () => {
    try {
      const result = (await action.mutateAsync({
        path: "/auto-robux/verify",
      })) as PassCheck;
      setCheck(result);
    } catch {
      setCheck(null);
    }
  };
  return (
    <section className="panel pricing-panel robux-panel">
      <div className="panel-head">
        <div>
          <h2>AutoRobux · Проверка геймпасса</h2>
          <p className="muted">
            Расчёт суммы и сверка цены собственного геймпасса через Roblox
            Creator API.
          </p>
        </div>
        <div className="robux-mark">R$</div>
      </div>
      <div className="robux-layout">
        <div className="robux-form">
          <PluginPart section="settings">
            <div className="two-fields">
              <Field label="ID мира (Universe)">
                <input
                  type="number"
                  min={1}
                  value={form.universe_id || ""}
                  onChange={(e) =>
                    update({ ...form, universe_id: Number(e.target.value) })
                  }
                />
              </Field>
              <Field label="ID геймпасса">
                <input
                  type="number"
                  min={1}
                  value={form.game_pass_id || ""}
                  onChange={(e) =>
                    update({ ...form, game_pass_id: Number(e.target.value) })
                  }
                />
              </Field>
            </div>
          </PluginPart>
          <PluginPart section="connection">
            <Field
              label="Cookie .ROBLOSECURITY"
              hint={
                query.data?.has_cookie
                  ? "Cookie сохранена в зашифрованном виде. Оставьте поле пустым, чтобы сохранить её."
                  : "Cookie нужна только для проверки собственного геймпасса."
              }
            >
              <div className="cookie-input">
                <input
                  type={showCookie ? "text" : "password"}
                  value={form.cookie}
                  onChange={(e) => update({ ...form, cookie: e.target.value })}
                  autoComplete="off"
                  placeholder="_|WARNING:-DO-NOT-SHARE…"
                />
                <button
                  title="Показать или скрыть"
                  onClick={() => setShowCookie(!showCookie)}
                >
                  {showCookie ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
                <button
                  title="Вставить"
                  onClick={() =>
                    void navigator.clipboard
                      .readText()
                      .then((value) => update({ ...form, cookie: value }))
                      .catch(() => toast.error("Буфер обмена недоступен"))
                  }
                >
                  <Clipboard size={16} />
                </button>
                <button
                  title="Копировать введённую cookie"
                  disabled={!form.cookie}
                  onClick={() =>
                    void navigator.clipboard
                      .writeText(form.cookie)
                      .then(() => toast.success("Cookie скопирована"))
                      .catch(() => toast.error("Буфер обмена недоступен"))
                  }
                >
                  <Copy size={16} />
                </button>
              </div>
            </Field>
          </PluginPart>
          <PluginPart section="settings">
            <Field label={`Сумма покупателю: ${form.target_amount} Robux`}>
              <input
                type="range"
                min={1}
                max={1000}
                value={Math.min(form.target_amount, 1000)}
                onChange={(e) =>
                  update({ ...form, target_amount: Number(e.target.value) })
                }
                className="robux-slider"
              />
            </Field>
            <div className="robux-cover">
              <div>
                <strong>Покрытие комиссии 30%</strong>
                <small>
                  Цена геймпасса будет рассчитана для указанной суммы.
                </small>
              </div>
              <button
                className={`mini-switch ${form.cover_fee ? "on" : ""}`}
                aria-label="Покрытие комиссии"
                aria-pressed={form.cover_fee}
                onClick={() => update({ ...form, cover_fee: !form.cover_fee })}
              />
            </div>
          </PluginPart>
          <div className="pricing-actions">
            <AutoSaveStatus state={auto} />
            <Button
              secondary
              disabled={
                action.isPending ||
                !query.data?.has_cookie ||
                managed === "connection" ||
                !!form.cookie ||
                form.universe_id !== query.data?.universe_id ||
                form.game_pass_id !== query.data?.game_pass_id ||
                form.target_amount !== query.data?.target_amount ||
                form.cover_fee !== query.data?.cover_fee
              }
              onClick={() => void verify()}
            >
              Проверить геймпасс
            </Button>
          </div>
        </div>
        <PluginPart section="settings">
          <div className="robux-summary">
            <span>Расчёт</span>
            <strong>{expected} R$</strong>
            <div>
              <ArrowDownRight size={17} /> Ожидаемое поступление: {net} R$
            </div>
            {check && (
              <div
                className={`robux-check ${check.matches ? "match" : "mismatch"}`}
              >
                {check.name || `Геймпасс #${check.game_pass_id}`}
                <br />
                Фактическая цена: {check.actual_price} R$ ·{" "}
                {check.matches
                  ? "совпадает"
                  : `ожидается ${check.expected_price} R$`}
              </div>
            )}
            <p>
              Веб-покупка геймпасса недоступна: прежний маршрут Roblox закрыт.
              Подтверждение покупки выполняйте на Roblox.
            </p>
          </div>
        </PluginPart>
      </div>
    </section>
  );
}
