import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Calculator,
  Copy,
  ExternalLink,
  LoaderCircle,
  Plus,
  Save,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import { request, useData } from "./api";
import type { Account, AppSettings, Lot } from "./types";
import { Button, Confirm, Field, Modal, Pending } from "./ui";

interface Control {
  name: string;
  label: string;
  value: string;
  kind: "select" | "checkbox" | "textarea" | "number" | "text";
  required: boolean;
  readonly?: boolean;
  checked_value?: string;
  max_length?: number;
  options: { value: string; label: string }[];
}
interface Editor {
  node_id: number;
  controls: Control[];
  buyer_price?: string | null;
}
type Result = {
  created?: number;
  requested?: number;
  error?: string;
  sync_pending?: boolean;
  changed_fields?: string[];
};
type PaymentPrice = { name: string; price: string; unit: string };
const languageOf = (name: string) => name.match(/\[(ru|en)\]$/)?.[1];
const textNames: Record<string, string> = {
  summary: "Краткое описание",
  desc: "Подробное описание",
  payment_msg: "Сообщение покупателю после оплаты",
};

export function LotEditor({
  accounts,
  lots = [],
  lot,
  onSaved,
  open: controlledOpen,
  onOpenChange,
  initialInactive = false,
}: {
  accounts: Account[];
  lots?: Lot[];
  lot?: Lot;
  onSaved: () => void;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  initialInactive?: boolean;
}) {
  const [internalOpen, setInternalOpen] = useState(false);
  const open = controlledOpen ?? internalOpen;
  const setOpen = (value: boolean) => {
    if (controlledOpen === undefined) setInternalOpen(value);
    onOpenChange?.(value);
  };
  const [accountId, setAccountId] = useState(String(lot?.account_id || ""));
  const [nodeId, setNodeId] = useState(String(lot?.node_id || ""));
  const [language, setLanguage] = useState("ru");
  const [fields, setFields] = useState<Record<string, string>>({});
  const [count, setCount] = useState(1);
  const [busy, setBusy] = useState<"save" | "copy" | "delete" | "preview" | null>(null);
  const [quotePrice, setQuotePrice] = useState("");
  const [batchResult, setBatchResult] = useState<Result | null>(null);
  const seeded = useRef("");
  const cache = useQueryClient();
  const [cost, setCost] = useState("");
  const [commission, setCommission] = useState("0");
  const [margin, setMargin] = useState("20");
  const endpoint = lot
    ? `/lots/${lot.id}/editor`
    : `/lots/editor?account_id=${accountId}&node_id=${nodeId}`;
  const editor = useQuery<Editor>({
    queryKey: ["lot-editor", endpoint],
    queryFn: () => request<Editor>(endpoint),
    enabled: open && (Boolean(lot) || Boolean(accountId && Number(nodeId) > 0)),
    refetchOnWindowFocus: false,
    staleTime: 0,
    retry: 1,
  });
  const settings = useData<AppSettings>("settings", "/settings/app", open);
  useEffect(() => {
    const timer = window.setTimeout(
      () => setQuotePrice(fields.price || ""),
      500,
    );
    return () => window.clearTimeout(timer);
  }, [fields.price]);
  const quotes = useQuery<PaymentPrice[]>({
    queryKey: ["lot-price-quote", accountId, nodeId, quotePrice],
    queryFn: () =>
      request<PaymentPrice[]>(
        `/lots/quote?account_id=${accountId}&node_id=${nodeId}&price=${encodeURIComponent(quotePrice)}`,
      ),
    enabled:
      open &&
      Number(quotePrice) > 0 &&
      Number(accountId) > 0 &&
      Number(nodeId) > 0,
    staleTime: 60000,
    refetchOnWindowFocus: false,
    retry: false,
  });
  useEffect(() => {
    if (settings.data) setCommission(String(settings.data.commission_percent));
  }, [settings.data]);
  useEffect(() => {
    if (
      open &&
      editor.data &&
      !editor.isFetching &&
      seeded.current !== endpoint
    ) {
      seeded.current = endpoint;
      setFields(
        Object.fromEntries(
          editor.data.controls.map((control) => [control.name, control.value]),
        ),
      );
    }
  }, [editor.data, editor.isFetching, endpoint, open]);
  const controls = editor.data?.controls || [];
  const localized = controls.filter((control) => languageOf(control.name));
  const common = controls.filter((control) => !languageOf(control.name));
  const commerceNames = [
    "auto_delivery",
    "secrets",
    "price",
    "amount",
    "active",
    "price_buyer",
    "buyer_price",
  ];
  const category = common.filter(
    (control) => !commerceNames.includes(control.name),
  );
  const suggestedPrice = useMemo(() => {
    const base = Number(cost.replace(",", ".")),
      fee = Number(commission.replace(",", ".")) / 100,
      profit = Number(margin.replace(",", ".")) / 100;
    return base > 0 && fee >= 0 && fee < 1 && profit >= 0
      ? Math.ceil(((base * (1 + profit)) / (1 - fee)) * 100) / 100
      : null;
  }, [cost, commission, margin]);
  function changeOpen(value: boolean) {
    if (busy) return;
    if (value) {
      seeded.current = "";
      setBatchResult(null);
      setLanguage("ru");
      setCount(1);
    }
    setOpen(value);
  }
  function renderControl(control: Control) {
    const value = fields[control.name] ?? control.value;
    const textKey = Object.keys(textNames).find((key) =>
      control.name.startsWith(`fields[${key}]`),
    );
    const label = textKey ? textNames[textKey] : control.label;
    const change = (next: string) => {
      setFields((old) => ({ ...old, [control.name]: next }));
      setBatchResult(null);
    };
    const hint =
      textKey === "summary"
        ? "Отображается прямо в таблице предложений."
        : textKey === "desc"
          ? "Можно не заполнять."
          : textKey === "payment_msg"
            ? "Будет отправлено каждому покупателю после оплаты."
            : undefined;
    if (control.kind === "checkbox")
      return (
        <label className="offer-checkbox" key={control.name}>
          <input
            type="checkbox"
            checked={
              initialInactive && !lot && control.name === "active"
                ? false
                : Boolean(value)
            }
            disabled={
              control.readonly ||
              !!busy ||
              (initialInactive && !lot && control.name === "active")
            }
            onChange={(event) =>
              change(event.target.checked ? control.checked_value || "on" : "")
            }
          />
          <span>{label}</span>
        </label>
      );
    return (
      <Field key={control.name} label={label} hint={hint}>
        {control.kind === "select" ? (
          <select
            aria-label={label}
            value={value}
            disabled={control.readonly || !!busy}
            onChange={(event) => change(event.target.value)}
          >
            {control.options.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        ) : control.kind === "textarea" ||
          ["desc", "payment_msg"].includes(textKey || "") ? (
          <textarea
            aria-label={label}
            rows={5}
            maxLength={control.max_length || undefined}
            value={value}
            readOnly={control.readonly}
            disabled={!!busy}
            onChange={(event) => change(event.target.value)}
          />
        ) : (
          <div
            className={
              ["price", "amount"].includes(control.name)
                ? "offer-unit-input"
                : ""
            }
          >
            <input
              aria-label={label}
              type={
                ["price", "amount"].includes(control.name)
                  ? "number"
                  : control.kind
              }
              step={control.name === "price" ? "0.01" : undefined}
              min={
                control.name === "price"
                  ? "0.01"
                  : control.name === "amount"
                    ? "0"
                    : undefined
              }
              maxLength={control.max_length || undefined}
              value={value}
              readOnly={control.readonly}
              disabled={!!busy}
              onChange={(event) => change(event.target.value)}
            />
            {control.name === "price" && <span>₽</span>}
            {control.name === "amount" && <span>шт.</span>}
          </div>
        )}
      </Field>
    );
  }
  async function save(mode: "save" | "copy" | "preview") {
    if (!editor.data || busy || seeded.current !== endpoint) return;
    const missing = controls.find(
      (control) =>
        control.required &&
        !control.readonly &&
        !(fields[control.name] ?? "").trim(),
    );
    if (missing) {
      if (languageOf(missing.name)) setLanguage(languageOf(missing.name)!);
      toast.error(`Заполните поле: ${missing.label}`);
      return;
    }
    if (
      (!lot || mode === "copy") &&
      (!Number.isInteger(count) || count < 1 || count > 20)
    ) {
      toast.error("Количество копий: целое число от 1 до 20");
      return;
    }
    setBusy(mode);
    setBatchResult(null);
    try {
      const editable = Object.fromEntries(
        controls
          .filter((control) => !control.readonly)
          .map((control) => [
            control.name,
            fields[control.name] ?? control.value,
          ]),
      );
      const copies = mode === "copy" && lot;
      if (!lot && initialInactive) editable.active = "";
      const result = await request<Result>(
        copies
          ? `/lots/${lot.id}/copies`
          : lot
            ? `/lots/${lot.id}${mode === "preview" ? "?dry_run=true" : ""}`
            : "/lots/create",
        copies || !lot ? "POST" : "PUT",
        copies
          ? { fields: editable, count }
          : lot
            ? { fields: editable }
            : {
                account_id: Number(accountId),
                node_id: Number(nodeId),
                count,
                fields: editable,
              },
      );
      if (mode === "preview") {
        toast.info(`Проверка пройдена. Изменений: ${result.changed_fields?.length ?? 0}. Сохранение не выполнялось.`);
        return;
      }
      if (result.error) {
        setBatchResult(result);
        toast.error(
          `Создано ${result.created} из ${result.requested}. ${result.error}`,
        );
      } else {
        toast.success(
          lot && !copies
            ? "Предложение сохранено на FunPay"
            : `Создано предложений: ${result.created}`,
        );
        if (copies) setBatchResult(result);
        else setOpen(false);
      }
      if (result.sync_pending)
        toast.info(
          "Предложения сохранены. Список FunPay обновится после синхронизации.",
        );
      await cache.invalidateQueries({ queryKey: ["lot-editor"] });
      onSaved();
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(null);
    }
  }
  async function remove() {
    if (!lot || busy) return;
    setBusy("delete");
    try {
      await request(`/lots/${lot.id}`, "DELETE");
      toast.success("Предложение удалено с FunPay");
      onSaved();
      setOpen(false);
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setBusy(null);
    }
  }
  const sellerPrice = controls.find((control) => control.name === "price");
  const buyerControl = controls.find((control) =>
    ["price_buyer", "buyer_price"].includes(control.name),
  );
  const currentQuotes =
    quotePrice === fields.price && Array.isArray(quotes.data)
      ? quotes.data
      : [];
  const rubPrices = currentQuotes
    .filter((row) => ["₽", "RUB", "руб."].includes(row.unit))
    .map((row) => Number(row.price.replace(/\s/g, "").replace(",", ".")))
    .filter((price) => price > 0);
  const buyerSnapshot = rubPrices.length
    ? Math.min(...rubPrices).toFixed(2)
    : fields.price === sellerPrice?.value
      ? editor.data?.buyer_price || buyerControl?.value
      : null;
  const ready =
    !!editor.data && seeded.current === endpoint && !editor.isFetching;
  return (
    <Modal
      className="offer-editor-modal"
      open={open}
      onOpenChange={changeOpen}
      trigger={
        controlledOpen !== undefined ? undefined : lot ? (
          <Button secondary>Редактировать</Button>
        ) : (
          <Button>
            <Plus size={15} /> Создать лот
          </Button>
        )
      }
      title={lot ? "Редактирование предложения" : "Создание предложения"}
      description={
        lot
          ? `Лот #${lot.funpay_lot_id} · ${lot.category_title}`
          : "Выберите аккаунт и категорию FunPay."
      }
    >
      {!lot && (
        <div className="two-fields offer-category-picker">
          <Field label="Аккаунт">
            <select
              aria-label="Аккаунт"
              value={accountId}
              disabled={!!busy}
              onChange={(event) => {
                setAccountId(event.target.value);
                setNodeId("");
                seeded.current = "";
              }}
            >
              <option value="">Выберите аккаунт</option>
              {accounts.map((account) => (
                <option key={account.id} value={account.id}>
                  {account.username}
                </option>
              ))}
            </select>
          </Field>
          <Field label="Категория" hint="ID из ссылки funpay.com/lots/123/">
            <input
              aria-label="Категория"
              type="number"
              list="lot-categories"
              min={1}
              value={nodeId}
              disabled={!accountId || !!busy}
              onChange={(event) => {
                setNodeId(event.target.value);
                seeded.current = "";
              }}
            />
            <datalist id="lot-categories">
              {[
                ...new Map(
                  lots
                    .filter((item) => item.account_id === Number(accountId))
                    .map((item) => [item.node_id, item.category_title]),
                ).entries(),
              ].map(([id, name]) => (
                <option key={id} value={id} label={name} />
              ))}
            </datalist>
          </Field>
        </div>
      )}
      {editor.isFetching && !ready && <Pending />}
      {editor.isError && (
        <p className="inline-error">{(editor.error as Error).message}</p>
      )}
      {ready && (
        <div className="offer-editor-body">
          {!!category.length && (
            <section className="offer-category-fields">
              <h3>Параметры предложения</h3>
              <div className="two-fields">{category.map(renderControl)}</div>
            </section>
          )}
          {!!localized.length && (
            <>
              <div
                className="offer-language-tabs"
                role="tablist"
                aria-label="Язык предложения"
              >
                {[
                  ["ru", "По-русски"],
                  ["en", "По-английски"],
                ]
                  .filter(([value]) =>
                    localized.some(
                      (control) => languageOf(control.name) === value,
                    ),
                  )
                  .map(([value, label]) => (
                    <button
                      key={value}
                      role="tab"
                      aria-selected={language === value}
                      aria-controls={`offer-text-${lot?.id || "new"}-${value}`}
                      id={`offer-tab-${lot?.id || "new"}-${value}`}
                      onClick={() => setLanguage(value)}
                    >
                      {label}
                    </button>
                  ))}
              </div>
              <section
                role="tabpanel"
                id={`offer-text-${lot?.id || "new"}-${language}`}
                aria-labelledby={`offer-tab-${lot?.id || "new"}-${language}`}
                className="offer-language-fields"
              >
                {localized
                  .filter((control) => languageOf(control.name) === language)
                  .map(renderControl)}
              </section>
            </>
          )}
          <section className="offer-commerce-fields">
            {controls
              .filter((control) => control.name === "auto_delivery")
              .map(renderControl)}
            {fields.auto_delivery &&
              controls
                .filter((control) => control.name === "secrets")
                .map(renderControl)}
            <div className="offer-pricing-stack">
              {sellerPrice && renderControl(sellerPrice)}
              {sellerPrice && (
                <Field
                  label="Цена за 1 шт. (для покупателя)"
                  hint="Минимальная цена среди доступных способов оплаты в рублях."
                >
                  <div className="offer-unit-input">
                    <input
                      aria-label="Цена для покупателя"
                      readOnly
                      value={buyerSnapshot || ""}
                      placeholder="Уточняется на FunPay"
                    />
                    <span>₽</span>
                  </div>
                </Field>
              )}
              {!fields.auto_delivery &&
                controls
                  .filter((control) => control.name === "amount")
                  .map(renderControl)}
            </div>
            {!!currentQuotes.length && (
              <div className="offer-payment-prices">
                <h4>Цена для покупателей</h4>
                <table>
                  <tbody>
                    {currentQuotes.map((method, index) => (
                      <tr key={`${method.name}-${index}`}>
                        <th>{method.name}</th>
                        <td>
                          {method.price} {method.unit}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {quotes.isError && (
              <small className="muted">
                FunPay не вернул расчёт комиссий. Цена продавца будет сохранена
                как указано.
              </small>
            )}
            {controls
              .filter((control) => control.name === "active")
              .map(renderControl)}
          </section>
          <details className="price-calculator">
            <summary>
              <Calculator size={16} /> Калькулятор цены
            </summary>
            <div className="two-fields">
              <Field label="Себестоимость, ₽">
                <input
                  type="number"
                  min={0}
                  step="0.01"
                  value={cost}
                  onChange={(event) => setCost(event.target.value)}
                />
              </Field>
              <Field label="Комиссия, %">
                <input
                  type="number"
                  min={0}
                  max={99}
                  step="0.1"
                  value={commission}
                  onChange={(event) => setCommission(event.target.value)}
                />
              </Field>
              <Field label="Наценка, %">
                <input
                  type="number"
                  min={0}
                  value={margin}
                  onChange={(event) => setMargin(event.target.value)}
                />
              </Field>
            </div>
            <Button
              secondary
              disabled={!suggestedPrice || !sellerPrice || !!busy}
              onClick={() =>
                setFields((old) => ({ ...old, price: String(suggestedPrice) }))
              }
            >
              Подставить {suggestedPrice?.toLocaleString("ru-RU") || "—"} ₽
            </Button>
          </details>
          <section className="offer-copy-panel">
            <div>
              <Copy size={19} />
              <div>
                <h3>
                  {lot ? "Создать копии предложения" : "Количество предложений"}
                </h3>
                <p>
                  {lot
                    ? "Копии используют заполненные поля обеих языковых вкладок. Исходный лот сохраняется отдельно."
                    : "Все предложения будут опубликованы с заполненными выше параметрами."}
                </p>
              </div>
            </div>
            <div className="offer-copy-actions">
              <Field label="Количество, от 1 до 20">
                <input
                  aria-label="Количество копий"
                  type="number"
                  min={1}
                  max={20}
                  step={1}
                  value={count}
                  disabled={!!busy}
                  onChange={(event) => {
                    setCount(Number(event.target.value));
                    setBatchResult(null);
                  }}
                />
              </Field>
              {lot && (
                <Button
                  secondary
                  disabled={
                    !!busy ||
                    !Number.isInteger(count) ||
                    count < 1 ||
                    count > 20 ||
                    !!batchResult
                  }
                  onClick={() => void save("copy")}
                >
                  {busy === "copy" ? (
                    <LoaderCircle className="spin" size={16} />
                  ) : (
                    <Copy size={16} />
                  )}{" "}
                  Создать копии ({count})
                </Button>
              )}
            </div>
            {controls.some((control) => control.name === "secrets") && lot && (
              <small>
                Пул ключей автоматической выдачи заполняется отдельно для каждой
                копии.
              </small>
            )}
            {batchResult && (
              <div
                className={
                  batchResult.error ? "inline-error" : "offer-copy-result"
                }
                role="status"
              >
                Создано {batchResult.created} из {batchResult.requested}.
                {batchResult.error &&
                  ` ${batchResult.error} Проверьте список FunPay перед повторной публикацией.`}
              </div>
            )}
          </section>
          <footer className="offer-editor-footer">
            {lot && (
              <a
                href={`https://funpay.com/lots/offerEdit?offer=${lot.funpay_lot_id}`}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink size={14} /> Открыть на FunPay
              </a>
            )}
              {lot && <Button secondary disabled={!!busy} onClick={() => void save("preview")}>Проверить изменения</Button>}
              <Button
                disabled={!!busy || (!lot && !!batchResult)}
              onClick={() => void save("save")}
            >
              {busy === "save" ? (
                <LoaderCircle className="spin" size={16} />
              ) : (
                <Save size={16} />
              )}
              {lot ? "Сохранить" : `Создать ${count} шт.`}
            </Button>
            {lot && (
              <Confirm
                trigger={
                  <Button className="offer-delete-button" disabled={!!busy}>
                    <Trash2 size={16} /> Удалить
                  </Button>
                }
                title="Удалить предложение с FunPay?"
                text={`Лот #${lot.funpay_lot_id} будет удалён. Созданные копии останутся.`}
                onConfirm={remove}
              />
            )}
          </footer>
        </div>
      )}
    </Modal>
  );
}
