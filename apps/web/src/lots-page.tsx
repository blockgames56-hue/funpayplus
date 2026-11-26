import { useAppearance } from "./appearance";
import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import {
  createColumnHelper,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { Reshaped } from "reshaped/bundle";
import {
  ArrowUpToLine,
  CheckCheck,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Filter,
  RefreshCw,
  Search,
  Trash2,
  X,
} from "lucide-react";
import { toast } from "sonner";
import Checkbox from "@/components/ui/reshaped-checkbox";
import { accountQuery, date, money, request, useAction, useData } from "./api";
import { useAccountStore, useLotStore } from "./store";
import { Button, Empty, Failure, Field, Modal, Pending, Toggle } from "./ui";
import { LotEditor } from "./lot-editor";
import type { Account, Lot } from "./types";

type Result = {
  id: number;
  title?: string;
  funpay_lot_id?: string;
  ok?: boolean;
  error?: string;
  deleted?: boolean;
  raised?: boolean;
  cooldown?: number;
  message?: string;
  skipped?: boolean;
};
type Operation = "raise" | "price" | "activate" | "deactivate" | "delete";
const column = createColumnHelper<Lot>();

function lotNoun(count: number, dative = false) {
  const single = count % 10 === 1 && count % 100 !== 11;
  if (dative) return single ? "лоту" : "лотам";
  if (single) return "лот";
  return [2, 3, 4].includes(count % 10) && ![12, 13, 14].includes(count % 100)
    ? "лота"
    : "лотов";
}

function Selection({
  label,
  checked,
  indeterminate,
  disabled,
  onChange,
}: {
  label: string;
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <Checkbox
      className="lot-checkbox"
      name="lots"
      size="large"
      checked={checked}
      indeterminate={indeterminate}
      disabled={disabled}
      inputAttributes={{ "aria-label": label }}
      onChange={({ checked }) => onChange(checked)}
    />
  );
}

export function LotsPage({ accountId }: { accountId?: number } = {}) {
  const lightTheme = useAppearance((s) => s.theme).startsWith("light");
  const globalAccount = useAccountStore((s) => s.selected);
  const selectedAccount = accountId ? String(accountId) : globalAccount;
  const query = useData<Lot[]>("lots", accountQuery("/lots", selectedAccount));
  const accounts = useData<Account[]>("accounts", "/accounts");
  const selection = useLotStore((s) => s.selection),
    setSelection = useLotStore((s) => s.setSelection);
  const [search, setSearch] = useState(""),
    [category, setCategory] = useState(""),
    [status, setStatus] = useState("");
  const [editing, setEditing] = useState<Lot | null>(null),
    [report, setReport] = useState<Result[]>([]);
  const [deleting, setDeleting] = useState<Lot[] | null>(null),
    [pricing, setPricing] = useState<Lot[] | null>(null),
    [percent, setPercent] = useState("5");
  const scope = useRef(selectedAccount),
    previousAccount = useRef(selectedAccount);
  scope.current = selectedAccount;
  const client = useQueryClient(),
    toggleAction = useAction();
  const bulk = useMutation({
    mutationFn: (payload: {
      ids: number[];
      action: Operation;
      percent?: number;
      dry_run?: boolean;
      scope: string;
    }) => {
      const { scope: _scope, ...body } = payload;
      return request<Result[]>("/lots/bulk", "POST", body);
    },
    onSuccess: (rows, variables) => {
      const done = new Set(rows.filter((r) => r.ok).map((r) => String(r.id)));
      const current = useLotStore.getState().selection;
      if (!variables.dry_run) setSelection(
        Object.fromEntries(
          Object.entries(current).filter(([id]) => !done.has(id)),
        ),
      );
      if (scope.current === variables.scope) setReport(rows);
      const problems = rows.filter((r) => r.error).length,
        success = done.size;
      if (problems)
        toast.warning(
          `Выполнено ${success} из ${rows.length}. Проверьте результаты ниже.`,
        );
      else if (!success)
        toast.info("FunPay ограничил поднятие. Срок ожидания показан ниже.");
      else
        toast.success(
          variables.dry_run ? `Проверено лотов: ${success}. Поднятие не выполнялось` : variables.action === "delete"
            ? `Удалено лотов: ${success}`
            : `Обработано лотов: ${success}`,
        );
    },
    onError: (error: Error) => toast.error(error.message),
    onSettled: () => {
      void client.invalidateQueries();
    },
  });
  const sync = useMutation({
    mutationFn: async () => {
      const ids = (accounts.data || [])
        .filter((a) => !selectedAccount || String(a.id) === selectedAccount)
        .map((a) => a.id);
      for (const id of ids) await request(`/accounts/${id}/sync-lots`, "POST");
    },
    onSuccess: () => toast.success("Лоты синхронизированы"),
    onError: (error: Error) => toast.error(error.message),
    onSettled: () => {
      void client.invalidateQueries();
    },
  });
  const busy = bulk.isPending || sync.isPending || toggleAction.isPending;
  useEffect(() => {
    if (previousAccount.current === selectedAccount) return;
    previousAccount.current = selectedAccount;
    setSelection({});
    setReport([]);
    setCategory("");
    setStatus("");
  }, [selectedAccount, setSelection]);
  useEffect(() => {
    if (!query.data || query.isFetching) return;
    const valid = new Set(query.data.map((r) => String(r.id))),
      current = useLotStore.getState().selection;
    const clean = Object.fromEntries(
      Object.entries(current).filter(
        ([id, checked]) => checked && valid.has(id),
      ),
    );
    if (Object.keys(clean).length !== Object.keys(current).length)
      setSelection(clean);
  }, [query.data, query.isFetching, setSelection]);

  const data = useMemo(
    () =>
      (query.data || []).filter(
        (r) =>
          (!category || String(r.node_id) === category) &&
          (!status || r.is_active === (status === "active")),
      ),
    [query.data, category, status],
  );
  const columns = useMemo(
    () => [
      column.display({
        id: "select",
        header: ({ table }) => (
          <Selection
            label="Выбрать все найденные лоты"
            checked={table.getIsAllPageRowsSelected()}
            indeterminate={table.getIsSomePageRowsSelected()}
            disabled={busy || !table.getRowModel().rows.length}
            onChange={(value) => table.toggleAllPageRowsSelected(value)}
          />
        ),
        cell: ({ row }) => (
          <Selection
            label={`Выбрать ${row.original.title}`}
            checked={row.getIsSelected()}
            disabled={busy}
            onChange={(value) => row.toggleSelected(value)}
          />
        ),
      }),
      column.accessor("title", {
        header: "Предложение",
        cell: (info) => (
          <div className="lot-title">
            <strong>{info.getValue()}</strong>
            <small>
              {info.row.original.category_title}{" "}
              <span>#{info.row.original.funpay_lot_id}</span>
            </small>
          </div>
        ),
      }),
      column.accessor("price", {
        header: "Цена, ₽",
        cell: (info) => <span className="mono">{money(info.getValue())}</span>,
      }),
      column.accessor("amount", {
        header: "Остаток",
        cell: (info) => <span className="mono">{info.getValue() == null ? "Неизвестно" : `${info.getValue()} шт.`}</span>,
      }),
      column.accessor("is_active", {
        header: "Статус",
        cell: (info) => (
          <span className={`status ${info.getValue() ? "green" : "amber"}`}>
            <i />
            {info.getValue() ? "Активен" : "Скрыт"}
          </span>
        ),
      }),
      column.accessor("auto_raise", {
        header: "Автоподнятие",
        cell: (info) => (
          <Toggle
            checked={info.getValue()}
            disabled={busy}
            label={`Автоподнятие ${info.row.original.title}`}
            onChange={(value) =>
              toggleAction.mutate({
                path: "/lots/bulk",
                body: {
                  ids: [info.row.original.id],
                  action: value ? "auto_raise_on" : "auto_raise_off",
                },
              })
            }
          />
        ),
      }),
      column.accessor("last_raised_at", {
        header: "Последнее поднятие",
        cell: (info) => (
          <span className="muted mono">{date(info.getValue())}</span>
        ),
      }),
      column.display({
        id: "edit",
        header: "Действия",
        cell: ({ row }) => (
          <Button
            secondary
            disabled={busy}
            onClick={() => setEditing(row.original)}
          >
            Редактировать
          </Button>
        ),
      }),
    ],
    [busy, toggleAction],
  );
  const table = useReactTable({
    data,
    columns,
    getRowId: (row) => String(row.id),
    state: { rowSelection: selection, globalFilter: search },
    onRowSelectionChange: (update) =>
      setSelection(
        typeof update === "function"
          ? update(useLotStore.getState().selection)
          : update,
      ),
    onGlobalFilterChange: setSearch,
    globalFilterFn: (row, _id, value: string) =>
      `${row.original.title} ${row.original.category_title} ${row.original.funpay_lot_id}`
        .toLowerCase()
        .includes(value.toLowerCase()),
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });
  const chosen = (query.data || []).filter((r) => selection[String(r.id)]),
    visible = table.getRowModel().rows;
  const visibleIds = new Set(visible.map((r) => r.original.id)),
    hiddenCount = chosen.filter((r) => !visibleIds.has(r.id)).length;
  const groupedErrors = new Map<string, Result[]>();
  for (const row of report.filter((r) => r.error))
    groupedErrors.set(row.error!, [
      ...(groupedErrors.get(row.error!) || []),
      row,
    ]);
  async function run(action: Operation, rows = chosen, dryRun = false) {
    await bulk.mutateAsync({
      ids: rows.map((r) => r.id),
      action,
      dry_run: dryRun,
      ...(action === "price" ? { percent: Number(percent) } : {}),
      scope: selectedAccount,
    });
  }
  const categories = [
    ...new Map(
      (query.data || []).map((r) => [r.node_id, r.category_title]),
    ).entries(),
  ];
  return (
    <Reshaped
      theme="slate"
      colorMode={lightTheme ? "light" : "dark"}
      scoped
      className="lots-workspace"
    >
      <div className="page-head">
        <div>
          <span className="eyebrow">Каталог / 04</span>
          <h1>Управление лотами</h1>
          <p>Выберите предложения для изменения цены, статуса или удаления.</p>
        </div>
        <div className="head-actions">
          <LotEditor
            accounts={accounts.data || []}
            lots={query.data || []}
            onSaved={() => {
              void query.refetch();
            }}
          />
          <Button
            secondary
            disabled={busy || !accounts.data?.length}
            onClick={() => sync.mutate()}
          >
            <RefreshCw size={15} className={sync.isPending ? "spin" : ""} />
            Синхронизировать
          </Button>
        </div>
      </div>
      {editing && (
        <LotEditor
          key={editing.id}
          open
          lot={editing}
          accounts={accounts.data || []}
          onOpenChange={(value) => {
            if (!value) setEditing(null);
          }}
          onSaved={() => {
            void query.refetch();
          }}
        />
      )}
      <div className="ws-categories" aria-label="Статусы лотов">
        {[
          ["", "Все лоты", query.data?.length || 0],
          [
            "active",
            "В продаже",
            query.data?.filter((l) => l.is_active).length || 0,
          ],
          [
            "inactive",
            "Выключенные",
            query.data?.filter((l) => !l.is_active).length || 0,
          ],
        ].map(([id, label, count]) => (
          <button
            key={id}
            className={status === id ? "active" : ""}
            onClick={() => setStatus(String(id))}
          >
            {label} · {count}
          </button>
        ))}
      </div>
      <div className="lots-catalog-layout">
        <aside className="ws-panel ws-pad lots-category-sidebar">
          <h3>
            Категории <small>{categories.length}</small>
          </h3>
          <button
            className={!category ? "active" : ""}
            onClick={() => setCategory("")}
          >
            Все категории <small>{query.data?.length || 0}</small>
          </button>
          {categories.map(([id, title]) => (
            <button
              key={id}
              className={category === String(id) ? "active" : ""}
              onClick={() => setCategory(String(id))}
            >
              {title}
              <small>
                {query.data?.filter((l) => l.node_id === id).length || 0}
              </small>
            </button>
          ))}
        </aside>
        <section className="panel lots-panel">
          <div className="table-toolbar lot-filters">
            <div className="search-field">
              <Search size={16} />
              <input
                aria-label="Поиск лотов"
                placeholder="Название, категория или ID лота…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <select
              aria-label="Сортировка лотов"
              value={table
                .getState()
                .sorting.map((v) => `${v.id}:${v.desc ? "desc" : "asc"}`)
                .join()}
              onChange={(e) => {
                const [id, dir] = e.target.value.split(":");
                table.setSorting(id ? [{ id, desc: dir === "desc" }] : []);
              }}
            >
              <option value="">По умолчанию</option>
              <option value="title:asc">По названию</option>
              <option value="price:asc">Цена по возрастанию</option>
              <option value="price:desc">Цена по убыванию</option>
              <option value="amount:asc">Сначала мало в наличии</option>
            </select>
            <label>
              <Filter size={14} />
              <select
                aria-label="Категория лотов"
                value={category}
                onChange={(e) => setCategory(e.target.value)}
              >
                <option value="">Все категории</option>
                {categories.map(([id, title]) => (
                  <option key={id} value={id}>
                    {title}
                  </option>
                ))}
              </select>
            </label>
            <select
              aria-label="Статус лотов"
              value={status}
              onChange={(e) => setStatus(e.target.value)}
            >
              <option value="">Все статусы</option>
              <option value="active">Активные</option>
              <option value="hidden">Скрытые</option>
            </select>
            <span className="muted">
              Найдено {visible.length} из {query.data?.length || 0}
            </span>
          </div>
          <div className="lot-selection-tools">
            <Button
              secondary
              disabled={busy || !visible.length}
              onClick={() => table.toggleAllPageRowsSelected(true)}
            >
              <CheckCheck size={15} />
              Выбрать найденные
            </Button>
            <Button
              secondary
              disabled={busy || !chosen.length}
              onClick={() => setSelection({})}
            >
              <X size={15} />
              Снять выбор
            </Button>
            <span>
              {chosen.length
                ? `Выбрано ${chosen.length}${hiddenCount ? ` · скрыто фильтром: ${hiddenCount}` : ""}`
                : "Отметьте нужные лоты слева"}
            </span>
          </div>
          {!!chosen.length && (
            <div className="lot-bulk-panel">
              <div className="bulk-toolbar">
                <span>Выбрано {chosen.length}</span>
                <Button
                  secondary
                  disabled={busy}
                  onClick={() => void run("raise").catch(() => {})}
                >
                  <ArrowUpToLine size={15} />
                  Поднять
                </Button>
                <Button
                  secondary
                  disabled={busy}
                  onClick={() => void run("raise", chosen, true).catch(() => {})}
                >
                  Проверить поднятие
                </Button>
                <Button
                  secondary
                  disabled={busy}
                  onClick={() => setPricing([...chosen])}
                >
                  Изменить цену
                </Button>
                <Button
                  secondary
                  disabled={busy}
                  onClick={() => void run("activate").catch(() => {})}
                >
                  Активировать
                </Button>
                <Button
                  secondary
                  disabled={busy}
                  onClick={() => void run("deactivate").catch(() => {})}
                >
                  Скрыть
                </Button>
                <Button
                  secondary
                  className="danger"
                  disabled={busy}
                  onClick={() => setDeleting([...chosen])}
                >
                  <Trash2 size={15} />
                  Удалить выбранные
                </Button>
                {bulk.isPending && <RefreshCw size={16} className="spin" />}
              </div>
              <p>
                Поднятие действует на категорию целиком. Изменение цены, статуса
                и удаление — только для выбранных лотов.
              </p>
            </div>
          )}
          {query.isPending ? (
            <Pending />
          ) : query.isError ? (
            <Failure error={query.error} retry={() => void query.refetch()} />
          ) : !query.data?.length ? (
            <Empty
              title="Каталог ещё не загружен"
              text="Синхронизируйте предложения подключённого аккаунта FunPay."
            />
          ) : !visible.length ? (
            <Empty title="Лоты не найдены" text="Измените поиск или фильтры." />
          ) : (
            <div className="table-scroll">
              <table>
                <thead>
                  {table.getHeaderGroups().map((group) => (
                    <tr key={group.id}>
                      {group.headers.map((header) => (
                        <th key={header.id}>
                          {header.column.getCanSort() ? (
                            <button
                              className="table-sort"
                              onClick={header.column.getToggleSortingHandler()}
                            >
                              {flexRender(
                                header.column.columnDef.header,
                                header.getContext(),
                              )}
                            </button>
                          ) : (
                            flexRender(
                              header.column.columnDef.header,
                              header.getContext(),
                            )
                          )}
                        </th>
                      ))}
                    </tr>
                  ))}
                </thead>
                <tbody>
                  {visible.map((row) => (
                    <tr key={row.id} data-selected={row.getIsSelected()}>
                      {row.getVisibleCells().map((cell) => (
                        <td key={cell.id}>
                          {flexRender(
                            cell.column.columnDef.cell,
                            cell.getContext(),
                          )}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>
      </div>
      {!!report.length && (
        <section
          className="panel lot-results"
          aria-label="Результаты обработки лотов"
        >
          <div className="panel-head">
            <h3>
              <CheckCircle2 size={18} />
              Результат обработки · {report.filter((r) => r.ok).length} из{" "}
              {report.length}
            </h3>
            <button
              className="icon-button"
              aria-label="Закрыть результаты"
              onClick={() => setReport([])}
            >
              <X size={16} />
            </button>
          </div>
          <p className="muted">
            Успешно обработанные лоты исключены из выбора.
          </p>
          {[...groupedErrors].map(([message, rows]) => (
            <div key={message} className="lot-result-error">
              <CircleAlert size={18} />
              <div>
                <strong>{message}</strong>
                <p>
                  {rows
                    .map(
                      (r) => `${r.title || "Лот"} #${r.funpay_lot_id || r.id}`,
                    )
                    .join(" · ")}
                </p>
              </div>
            </div>
          ))}
          {report
            .filter((r) => !r.error && r.raised === false)
            .map((r) => (
              <div className="lot-result-wait" key={r.id}>
                <Clock3 size={16} />
                <p>
                  {r.title}: {r.message || "Поднятие пока недоступно"}
                  {r.cooldown
                    ? ` · Повтор через ${Math.ceil(r.cooldown / 60)} мин.`
                    : ""}
                </p>
              </div>
            ))}
        </section>
      )}
      <Modal
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open && !bulk.isPending) setDeleting(null);
        }}
        title="Удалить выбранные лоты?"
        description={`Предложений для удаления: ${deleting?.length || 0}. Удаление на FunPay нельзя отменить.`}
      >
        <div className="lot-confirm-list">
          {deleting?.map((r) => (
            <div key={r.id}>
              <strong>{r.title}</strong>
              <small>
                {r.category_title} · #{r.funpay_lot_id}
              </small>
            </div>
          ))}
        </div>
        <p className="muted">
          Лоты, привязанные к плагинам, потребуют сначала удалить привязку в
          настройках плагина. Оплаченные заказы не отменяются.
        </p>
        <div className="form-actions">
          <Button
            secondary
            disabled={bulk.isPending}
            onClick={() => setDeleting(null)}
          >
            Отмена
          </Button>
          <Button
            className="danger"
            disabled={bulk.isPending}
            onClick={() => {
              if (deleting)
                void run("delete", deleting)
                  .then(() => setDeleting(null))
                  .catch(() => {});
            }}
          >
            <Trash2 size={16} />
            {bulk.isPending
              ? "Удаляем…"
              : `Удалить ${deleting?.length || 0} ${lotNoun(deleting?.length || 0)}`}
          </Button>
        </div>
      </Modal>
      <Modal
        open={!!pricing}
        onOpenChange={(open) => {
          if (!open && !bulk.isPending) setPricing(null);
        }}
        title="Массовое изменение цены"
        description={`Изменятся цены только ${pricing?.length || 0} выбранных лотов.`}
      >
        <Field
          label="Процент изменения"
          hint="Отрицательное число уменьшает цену, положительное — увеличивает."
        >
          <input
            aria-label="Процент изменения"
            type="number"
            min={-99}
            max={1000}
            value={percent}
            onChange={(e) => setPercent(e.target.value)}
          />
        </Field>
        <div className="lot-confirm-list">
          {pricing?.map((r) => (
            <div key={r.id}>
              <strong>{r.title}</strong>
              <small>
                #{r.funpay_lot_id} · {money(r.price)} →{" "}
                {money(
                  Math.round(
                    Number(r.price) * (1 + Number(percent) / 100) * 100,
                  ) / 100,
                )}
              </small>
            </div>
          ))}
        </div>
        <div className="form-actions">
          <Button
            secondary
            disabled={bulk.isPending}
            onClick={() => setPricing(null)}
          >
            Отмена
          </Button>
          <Button
            disabled={
              bulk.isPending ||
              !percent.trim() ||
              !Number.isFinite(Number(percent)) ||
              Number(percent) < -99 ||
              Number(percent) > 1000
            }
            onClick={() => {
              if (pricing)
                void run("price", pricing)
                  .then(() => setPricing(null))
                  .catch(() => {});
            }}
          >
            {bulk.isPending
              ? "Сохраняем…"
              : `Применить к ${pricing?.length || 0} ${lotNoun(pricing?.length || 0, true)}`}
          </Button>
        </div>
      </Modal>
    </Reshaped>
  );
}
