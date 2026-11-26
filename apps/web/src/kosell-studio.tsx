import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Calculator,
  FilePlus2,
  LoaderCircle,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";
import { request, useAction, useData } from "./api";
import { Button, Empty, Field, Modal } from "./ui";
import type { Lot } from "./types";
import type {
  Batch,
  KoState,
  StudioForm,
  StudioTemplate,
  Texts,
} from "./kosell-types";

const titles: Record<keyof Texts, string> = {
  title: "Краткое описание RU",
  description: "Подробное описание RU",
  payment: "Сообщение после оплаты RU",
  title_en: "Краткое описание EN",
  description_en: "Подробное описание EN",
  payment_en: "Сообщение после оплаты EN",
};
const noop = () => {};
function readDraft(): {
  form?: StudioForm;
  account?: string;
  node?: string;
  hoursText?: string;
} {
  try {
    return JSON.parse(sessionStorage.getItem("kosell-studio-draft") || "{}");
  } catch {
    return {};
  }
}
export default function KosellStudio({ state }: { state: KoState }) {
  const lots = useData<Lot[]>("lots", "/lots");
  const templates = useData<StudioTemplate[]>(
    "kosell-studio-templates",
    "/plugins/kosell/studio/templates",
  );
  const action = useAction();
  const [form, setForm] = useState<StudioForm>(() => ({
    ...state.studio_defaults,
    source_lot_id: 0,
    product_ids: [],
    sources: {},
    hours: [24],
    currency: "RUB",
    price_mode: "markup",
    markup: 40,
    fixed_price: 100,
    amount: 999999,
    ...readDraft().form,
  }));
  const [account, setAccount] = useState(() => readDraft().account || ""),
    [node, setNode] = useState(() => readDraft().node || ""),
    [search, setSearch] = useState("");
  const [hoursText, setHoursText] = useState(
    () => readDraft().hoursText || "24",
  );
  useEffect(() => {
    try {
      sessionStorage.setItem(
        "kosell-studio-draft",
        JSON.stringify({ form, account, node, hoursText }),
      );
    } catch {
      /* Session storage can be unavailable in a private window. */
    }
  }, [form, account, node, hoursText]);
  const [template, setTemplate] = useState(""),
    [name, setName] = useState("");
  const [deleteTemplate, setDeleteTemplate] = useState(false);
  const [batchId, setBatchId] = useState(
    () => sessionStorage.getItem("kosell-studio-batch") || "",
  );
  const [confirm, setConfirm] = useState(false);
  const batch = useQuery<Batch>({
    queryKey: ["kosell-studio-batch", batchId],
    queryFn: () => request<Batch>(`/plugins/kosell/studio/batches/${batchId}`),
    enabled: !!batchId,
    refetchInterval: (query) =>
      query.state.data?.state === "applying" ? 2000 : false,
    retry: false,
  });
  const [language, setLanguage] = useState<"ru" | "en">("ru");
  const set = <K extends keyof StudioForm>(field: K, value: StudioForm[K]) =>
    setForm((old) => ({ ...old, [field]: value }));
  useEffect(() => {
    if (batchId) sessionStorage.setItem("kosell-studio-batch", batchId);
    else sessionStorage.removeItem("kosell-studio-batch");
  }, [batchId]);
  const parseHours = () => {
    const hours = [
      ...new Set(
        hoursText
          .split(/[,;\s]+/)
          .filter(Boolean)
          .map(Number),
      ),
    ];
    return hours.length > 0 &&
      hours.length <= 12 &&
      hours.every((h) => Number.isInteger(h) && h >= 1 && h <= 8760)
      ? hours
      : null;
  };
  const saveTemplate = async (update: boolean) => {
    const hours = parseHours();
    if (!hours) return;
    await action.mutateAsync({
      path: `/plugins/kosell/studio/templates${update ? `/${template}` : ""}`,
      method: update ? "PUT" : "POST",
      body: { ...form, name, hours },
      success: update ? "Шаблон обновлён" : "Шаблон сохранён",
    });
  };
  const selected = templates.data?.find((t) => t.id === template);
  const applyTemplate = () => {
    if (!selected) return;
    const { id: _, name: __, ...saved } = selected;
    setForm((old) => ({ ...old, ...saved }));
    setHoursText(saved.hours.join(", "));
    setName(selected.name);
  };
  const calculate = async () => {
    const hours = parseHours();
    if (!hours) return;
    const value = (await action.mutateAsync({
      path: "/plugins/kosell/studio/preview",
      body: {
        ...form,
        source_lot_id:
          form.source_lot_id || Object.values(form.sources)[0] || 0,
        hours,
      },
      success: "Цены и тексты рассчитаны",
    })) as Batch;
    setBatchId(value.id);
  };
  const sources =
    lots.data?.filter(
      (l) =>
        l.account_id === Number(account) &&
        (!node || l.node_id === Number(node)),
    ) || [];
  const products = state.catalog.items.filter((p) =>
    `${p.name} ${p.id}`.toLowerCase().includes(search.toLowerCase()),
  );
  return (
    <div className="kosell-stack">
      <section className="panel kosell-card">
        <div className="kosell-row">
          <div>
            <h3>
              <FilePlus2 size={19} /> Студия лотов KoSell
            </h3>
            <p>
              Рассчитайте цены и проверьте тексты. Затем создайте лоты одной
              партией.
            </p>
          </div>
          <Button
            disabled={
              action.isPending ||
              (!form.source_lot_id &&
                !(
                  form.product_ids.length > 1 &&
                  form.product_ids.every((id) => form.sources[id])
                )) ||
              !form.product_ids.length ||
              !parseHours() ||
              batch.data?.state === "applying"
            }
            onClick={() => void calculate().catch(noop)}
          >
            {action.isPending ? (
              <LoaderCircle size={16} className="animate-spin" />
            ) : (
              <Calculator size={16} />
            )}
            Рассчитать и показать
          </Button>
        </div>
        <p>
          Новые лоты создаются выключенными. KoSell включит их после проверки
          остатка и цены, когда плагин запущен. Исходные лоты сохраняются.
        </p>
      </section>
      <section className="panel kosell-card">
        <h3>Сохранённые шаблоны</h3>
        <p>
          Общий текстовый шаблон можно использовать для разных игр. Сохраняются
          тексты RU/EN, сроки, способ цены, наценка и наличие. Продавец, товар и
          исходные лоты выбираются отдельно.
        </p>
        <div className="kosell-form-grid">
          <Field label="Шаблон KoSell">
            <select
              aria-label="Шаблон KoSell"
              value={template}
              onChange={(e) => {
                setTemplate(e.target.value);
                setName(
                  templates.data?.find((t) => t.id === e.target.value)?.name ||
                    "",
                );
              }}
            >
              <option value="">Выберите шаблон</option>
              {templates.data?.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Название шаблона">
            <input
              value={name}
              maxLength={100}
              onChange={(e) => setName(e.target.value)}
              placeholder="Например: аренда Steam на сутки"
            />
          </Field>
        </div>
        <div className="kosell-actions">
          <Button secondary disabled={!template} onClick={applyTemplate}>
            Применить шаблон
          </Button>
          <Button
            secondary
            disabled={!name.trim() || action.isPending || !parseHours()}
            onClick={() => void saveTemplate(false).catch(noop)}
          >
            <Save size={15} />
            Сохранить новый
          </Button>
          <Button
            secondary
            disabled={
              !template || !name.trim() || action.isPending || !parseHours()
            }
            onClick={() => void saveTemplate(true).catch(noop)}
          >
            Обновить шаблон
          </Button>
          <Button
            secondary
            disabled={!selected || !name.trim() || action.isPending}
            onClick={() =>
              action.mutate({
                path: `/plugins/kosell/studio/templates/${template}`,
                method: "PUT",
                body: { ...selected, name },
                success: "Шаблон переименован",
              })
            }
          >
            Переименовать
          </Button>
          <Button
            secondary
            disabled={!template || action.isPending}
            onClick={() => setDeleteTemplate(true)}
          >
            <Trash2 size={15} />
            Удалить
          </Button>
          <Button
            secondary
            disabled={templates.isFetching}
            onClick={() => void templates.refetch()}
          >
            <RefreshCw size={15} />
            Обновить список
          </Button>
        </div>
        {templates.isError && (
          <p className="kosell-warn">{templates.error.message}</p>
        )}
      </section>
      <section className="panel kosell-studio-layout">
        <aside className="kosell-studio-sidebar">
          <h4>Источник лотов</h4>
          <p>
            Исходный лот задаёт категорию и её обязательные поля. Тексты и сроки
            заменятся данными из студии.
          </p>
          <Field label="Аккаунт FunPay">
            <select
              aria-label="Аккаунт FunPay"
              value={account}
              onChange={(e) => {
                setAccount(e.target.value);
                set("source_lot_id", 0);
              }}
            >
              <option value="">Выберите аккаунт</option>
              {state.accounts.map((a) => (
                <option value={a.id} key={a.id}>
                  {a.username || a.title}
                </option>
              ))}
            </select>
          </Field>
          <Field label="ID категории">
            <input
              type="number"
              min={1}
              value={node}
              onChange={(e) => setNode(e.target.value)}
              placeholder="Все категории аккаунта"
            />
          </Field>
          <Button
            secondary
            disabled={!account || action.isPending}
            onClick={() =>
              action.mutate({
                path: `/accounts/${account}/sync-lots`,
                success: "Лоты аккаунта обновлены",
              })
            }
          >
            <RefreshCw size={15} />
            Загрузить лоты
          </Button>
          <Field label="Исходный лот FunPay">
            <select
              aria-label="Исходный лот FunPay"
              value={form.source_lot_id || ""}
              onChange={(e) => set("source_lot_id", Number(e.target.value))}
            >
              <option value="">Выберите лот своей категории</option>
              {sources.map((l) => (
                <option value={l.id} key={l.id}>
                  #{l.funpay_lot_id} · {l.title}
                </option>
              ))}
            </select>
          </Field>
          <hr />
          <h4>Игра и сроки</h4>
          <Field label="Поиск игры KoSell">
            <input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Название или ID игры"
            />
          </Field>
          <Field label="Товар KoSell">
            <select
              aria-label="Товар KoSell"
              value={form.product_ids.length === 1 ? form.product_ids[0] : ""}
              onChange={(e) =>
                set(
                  "product_ids",
                  e.target.value ? [Number(e.target.value)] : [],
                )
              }
            >
              <option value="">
                {form.product_ids.length > 1
                  ? `Выбрано ${form.product_ids.length} игр`
                  : "Выберите товар"}
              </option>
              {products.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name} · {p.available_accounts} шт.
                </option>
              ))}
            </select>
          </Field>
          <Field
            label="Сроки аренды, часы"
            hint="До 12 уникальных значений через запятую: 1, 5, 24, 48"
          >
            <input
              value={hoursText}
              aria-label="Сроки аренды, часы"
              onChange={(e) => setHoursText(e.target.value)}
              aria-invalid={!parseHours()}
            />
          </Field>
          {!parseHours() && (
            <p className="kosell-warn">
              Введите от 1 до 12 целых сроков, каждый от 1 до 8760 часов.
            </p>
          )}
          <Field label="Валюта цены FunPay">
            <input readOnly value="RUB" />
          </Field>
          <Field label="Способ цены">
            <select
              value={form.price_mode}
              onChange={(e) =>
                set("price_mode", e.target.value as "markup" | "fixed")
              }
            >
              <option value="markup">Наценка к KoSell</option>
              <option value="fixed">Фиксированная цена</option>
            </select>
          </Field>
          {form.price_mode === "markup" ? (
            <Field label="Наценка, %">
              <input
                type="number"
                min={0}
                max={10000}
                value={form.markup}
                onChange={(e) => set("markup", Number(e.target.value))}
              />
            </Field>
          ) : (
            <Field label="Цена лота, ₽">
              <input
                type="number"
                min={1}
                value={form.fixed_price}
                onChange={(e) => set("fixed_price", Number(e.target.value))}
              />
            </Field>
          )}
          <Field label="Наличие в лоте, шт.">
            <input
              type="number"
              min={1}
              max={999999999}
              value={form.amount}
              onChange={(e) => set("amount", Number(e.target.value))}
            />
          </Field>
        </aside>
        <div className="kosell-studio-texts">
          <h3>Тексты лота</h3>
          <p>
            Один шаблон — для разных игр. Значения подставятся отдельно для
            каждого товара и срока. Окончательный текст можно проверить в
            предпросмотре.
          </p>
          <div className="kosell-tags">
            {state.studio_tags.map((tag) => (
              <code key={tag}>{`{${tag}}`}</code>
            ))}
          </div>
          <div className="kosell-segments">
            <button
              className={language === "ru" ? "active" : ""}
              onClick={() => setLanguage("ru")}
            >
              По-русски
            </button>
            <button
              className={language === "en" ? "active" : ""}
              onClick={() => setLanguage("en")}
            >
              По-английски
            </button>
          </div>
          {(Object.keys(titles) as (keyof Texts)[])
            .filter((key) =>
              language === "en" ? key.endsWith("_en") : !key.endsWith("_en"),
            )
            .map((key) => (
              <Field
                key={key}
                label={titles[key]}
                hint={`${form[key].length} / ${key.startsWith("title") ? 100 : key.startsWith("description") ? 20000 : 5000}`}
              >
                <textarea
                  rows={key.startsWith("title") ? 2 : 7}
                  maxLength={
                    key.startsWith("title")
                      ? 100
                      : key.startsWith("description")
                        ? 20000
                        : 5000
                  }
                  value={form[key]}
                  onChange={(e) => set(key, e.target.value)}
                />
              </Field>
            ))}
        </div>
      </section>
      <section className="panel kosell-card">
        <h3>Лоты для нескольких игр</h3>
        <p>
          Выберите товары и укажите исходный лот для каждой игры. Общие сроки и
          тексты берутся из студии выше. Не более 120 сочетаний игр и сроков в
          одной партии.
        </p>
        <div className="kosell-actions">
          <Button
            secondary
            disabled={!state.catalog.items.length}
            onClick={() =>
              set(
                "product_ids",
                state.catalog.items.map((p) => p.id),
              )
            }
          >
            Подготовить весь каталог
          </Button>
          <Button
            secondary
            onClick={() => {
              set("product_ids", []);
              set("sources", {});
            }}
          >
            Снять выбор
          </Button>
          <span>
            Игр: {form.product_ids.length} · лотов:{" "}
            {form.product_ids.length * (parseHours()?.length || 0)}
          </span>
        </div>
        <div className="kosell-catalog-pick">
          {products.map((p) => (
            <label key={p.id}>
              <input
                type="checkbox"
                checked={form.product_ids.includes(p.id)}
                onChange={(e) =>
                  set(
                    "product_ids",
                    e.target.checked
                      ? [...form.product_ids, p.id]
                      : form.product_ids.filter((id) => id !== p.id),
                  )
                }
              />
              <span>
                {p.name}
                <small>
                  #{p.id} · {p.available_accounts} шт. · {p.min_hours}–
                  {p.max_hours} ч
                </small>
              </span>
            </label>
          ))}
        </div>
        {form.product_ids.length > 1 && (
          <div className="kosell-game-sources">
            {form.product_ids.map((id) => (
              <Field
                key={id}
                label={`Исходный лот: ${state.catalog.items.find((p) => p.id === id)?.name || id}`}
              >
                <select
                  value={form.sources[id] || ""}
                  onChange={(e) =>
                    set("sources", {
                      ...form.sources,
                      [id]: Number(e.target.value),
                    })
                  }
                >
                  <option value="">Выберите лот нужной категории</option>
                  {lots.data?.map((l) => (
                    <option key={l.id} value={l.id}>
                      {
                        state.accounts.find((a) => a.id === l.account_id)
                          ?.username
                      }{" "}
                      · #{l.funpay_lot_id} · {l.title}
                    </option>
                  ))}
                </select>
              </Field>
            ))}
          </div>
        )}
      </section>
      {batchId && (
        <section className="panel kosell-card">
          <div className="kosell-row">
            <h3>Предпросмотр партии</h3>
            <Button
              secondary
              onClick={() => setBatchId("")}
              disabled={batch.data?.state === "applying"}
            >
              Закрыть предпросмотр
            </Button>
          </div>
          {batch.isError ? (
            <p className="kosell-warn">{batch.error.message}</p>
          ) : batch.isLoading ? (
            <p>Загружаем расчёт…</p>
          ) : (
            batch.data && (
              <>
                <p>
                  Состояние:{" "}
                  <strong>
                    {(
                      {
                        preview: "Расчёт готов",
                        applying: "Создаём лоты…",
                        done: "Готово",
                        manual_check: "Требуется сверка",
                        failed: "Остановлено до публикации",
                      } as Record<string, string>
                    )[batch.data.state] || batch.data.state}
                  </strong>
                </p>
                <div className="kosell-table">
                  <table>
                    <thead>
                      <tr>
                        <th>Игра / срок</th>
                        <th>Заголовок</th>
                        <th>Закупка</th>
                        <th>Продажа</th>
                        <th>Результат</th>
                      </tr>
                    </thead>
                    <tbody>
                      {batch.data.rows.map((r, i) => (
                        <tr key={i}>
                          <td>
                            {r.game}
                            <small>
                              {r.hours} ч · остаток {r.stock}
                            </small>
                          </td>
                          <td>
                            <strong>{r.texts.title}</strong>
                            <details>
                              <summary>Проверить тексты RU/EN</summary>
                              <div className="kosell-preview-text">
                                {(Object.keys(titles) as (keyof Texts)[]).map(
                                  (key) => (
                                    <div key={key}>
                                      <b>{titles[key]}</b>
                                      <p>{r.texts[key] || "—"}</p>
                                    </div>
                                  ),
                                )}
                              </div>
                            </details>
                          </td>
                          <td>{r.cost} ₽</td>
                          <td>{r.price} ₽</td>
                          <td>
                            {r.funpay_item_id
                              ? `Создан #${r.funpay_item_id}`
                              : r.error ||
                                (
                                  {
                                    planned: "Ожидает",
                                    sending: "Отправлен запрос",
                                    failed: "Ошибка проверки",
                                    manual_check: "Нужна сверка",
                                  } as Record<string, string>
                                )[r.state] ||
                                r.state}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                <div className="kosell-actions">
                  {batch.data.state === "preview" && (
                    <Button
                      onClick={() => setConfirm(true)}
                      disabled={action.isPending}
                    >
                      <FilePlus2 size={16} />
                      Создать {batch.data.rows.length} лотов
                    </Button>
                  )}
                  {["manual_check", "applying"].includes(batch.data.state) && (
                    <Button
                      secondary
                      disabled={
                        action.isPending || batch.data.state === "applying"
                      }
                      onClick={() =>
                        void action
                          .mutateAsync({
                            path: `/plugins/kosell/studio/batches/${batchId}/reconcile`,
                            success: "Сверка партии завершена",
                          })
                          .then(() => batch.refetch())
                          .catch(noop)
                      }
                    >
                      Сверить созданные лоты
                    </Button>
                  )}
                  {["manual_check", "failed"].includes(batch.data.state) &&
                    !batch.data.rows.some((row) =>
                      ["sending", "manual_check"].includes(row.state),
                    ) && (
                      <Button
                        secondary
                        disabled={action.isPending}
                        onClick={() =>
                          void action
                            .mutateAsync({
                              path: `/plugins/kosell/studio/batches/${batchId}/continue`,
                              success: "Продолжаем только несозданные позиции",
                            })
                            .then(() => batch.refetch())
                            .catch(noop)
                        }
                      >
                        Продолжить несозданные позиции
                      </Button>
                    )}
                </div>
              </>
            )
          )}
        </section>
      )}
      <Modal
        title="Создать рассчитанные лоты?"
        description="Будет создана новая партия на FunPay. Лоты сначала выключены, привязки к KoSell сохранятся автоматически. Повторная отправка этой партии заблокирована."
        open={confirm}
        onOpenChange={setConfirm}
      >
        <p>Позиций: {batch.data?.rows.length}. Исходные лоты не изменяются.</p>
        <Button
          disabled={action.isPending}
          onClick={() =>
            void action
              .mutateAsync({
                path: `/plugins/kosell/studio/batches/${batchId}/create`,
                success: "Создание партии запущено",
              })
              .then(() => {
                setConfirm(false);
                void batch.refetch();
              })
              .catch(noop)
          }
        >
          Подтвердить создание
        </Button>
      </Modal>
      <Modal
        title="Удалить текстовый шаблон?"
        description="Созданные лоты и их привязки сохранятся."
        open={deleteTemplate}
        onOpenChange={setDeleteTemplate}
      >
        <Button
          disabled={action.isPending}
          onClick={() =>
            void action
              .mutateAsync({
                path: `/plugins/kosell/studio/templates/${template}`,
                method: "DELETE",
                success: "Шаблон удалён",
              })
              .then(() => {
                setDeleteTemplate(false);
                setTemplate("");
              })
              .catch(noop)
          }
        >
          Удалить шаблон
        </Button>
      </Modal>
      {!state.catalog.items.length && (
        <Empty
          title="Каталог KoSell пока не загружен"
          text="Откройте «Настройки → Подключение», сохраните API-ключ и проверьте KoSell."
        />
      )}
    </div>
  );
}
